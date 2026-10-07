"""PF-A2.1 acceptance and regression tests: preflight installer, staged control/config generations, journaled switch.

Case mapping (PF-A2.1 SPEC section 6.1; design r3 acceptance cases A2-T01..A2-T04):
  CS-1..CS-3   candidate read as data                    -> CandidateSource
  PF-1..PF-9   preflight collects every conflict (A2-T01) -> Preflight
  RI-1..RI-4   repository installer (init)               -> RepositoryInstaller
  CN-1..CN-4   cancellation (A2-T02)                     -> Cancellation
  RS-1..RS-4   real signals and SIGKILL (A2-T02/T03)      -> RealSignals (installed CLI)
  CM-1..CM-8   forked crash matrix (A2-T03)              -> CrashMatrix
  SM-1..SM-2   smoke validates live configuration        -> SmokeConfig
  IP-1         identity preservation (A2-T04 offline)    -> IdentityPreservation
  CC-1..CC-7   concurrency and lock order                -> Concurrency
  LG-1..LG-3   the v2.5 operation lock                   -> LegacyLock
  SC-1..SC-3   select a retained release                 -> SelectControl
  LB-1..LB-3   global launcher                           -> LauncherBinding
  LM-1..LM-3   legacy v2.5 migration                     -> LegacyMigration
  SC-S1        wire schema                               -> Schema
  AU-1..AU-16  PF-A2.1 audit regressions (audit-findings.json AF-01..AF-18) -> AuditRegressions

Everything runs as uid 0 in disposable temporary roots. Docker is never contacted: the registered ``docker`` tool is
``tests/fake_docker.py``. "Forked crash" = the test forks; the child patches a pf_install seam
(``_journal_write`` or ``_apply_effect``) to end with ``os._exit(137)`` before or after the real call; the parent
then resumes through the installed CLI. When ``PF_A21_EVIDENCE`` names a directory, transcripts, the crash matrix
and before/after identities are written there (checkpoint evidence only; never asserted).
"""
import ast
import contextlib
import copy
import errno
import fcntl
import grp
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_install = pf.pf_install
pf_instance = pfx.pf_instance
pf_bootstrap = pfx.pf_bootstrap
PACKAGE = pfx.PACKAGE
REPO_PACKAGE = pfx.REPO_PACKAGE
GROUP = grp.getgrgid(os.getgid()).gr_name
ROOT_REQUIRED = unittest.skipUnless(os.geteuid() == 0, "protected fixtures require root ownership (uid 0)")
PY = os.path.realpath(sys.executable)
PROBE = list(pf.pf_docker.DAEMON_PROBE_ARGV[1:])
CRASH_ROWS = []


def record_evidence(name, payload):
    directory = os.environ.get("PF_A21_EVIDENCE")
    if not directory:
        return
    path = Path(directory) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


def crash_row(**row):
    CRASH_ROWS.append(row)
    record_evidence("INSTALL_CRASH_MATRIX", CRASH_ROWS)


class Script:
    """Scripted operator answers. ``EOFError``/``KeyboardInterrupt`` entries raise; a callable is called."""

    def __init__(self, answers, sink):
        self.answers = list(answers)
        self.sink = sink
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        self.sink.write(prompt + "\n")
        if not self.answers:
            raise EOFError
        item = self.answers.pop(0)
        if item in (EOFError, KeyboardInterrupt):
            raise item
        return item() if callable(item) else item


def interaction(answers=(), sink=None):
    sink = sink if sink is not None else io.StringIO()
    return pf_install.Interaction(lambda: False, Script(answers, sink), lambda text: sink.write(text + "\n"))


def silent():
    return interaction(())


def snapshot(*roots):
    return pfx.snapshot_tree(*[root for root in roots if os.path.lexists(str(root))])


def op_dirs(root):
    directory = Path(root) / "install-operations"
    return sorted(os.listdir(str(directory))) if directory.exists() else []


class InstallBase(unittest.TestCase):
    """A content-addressed installation root with registered instances (``instances``) and the fake daemon."""

    instances = ("a", "b")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.layout = pfx.install_root(self.base, release_id=pfx.content_release_id())
        self.root = self.layout.root
        self.old_release = self.layout.release_dir
        self.fake = pfx.install_fake_docker(self.layout)
        self.contexts, self.paths = {}, {}
        for slug in self.instances:
            self.add_instance(slug)
        self.fake.clear_calls()

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

    def add_instance(self, slug, *, workspace_name="repo", project=None):
        paths = pfx.data_home(self.base / slug, project=project or "pf-" + slug, group=GROUP)
        if workspace_name != "repo":
            target = paths["workspace"].parent / workspace_name
            os.rename(paths["workspace"], target)
            paths["workspace"] = target
        context = pfx.register(self.layout, slug, paths, project=project or "pf-" + slug)
        self.contexts[slug], self.paths[slug] = context, paths
        return context

    # -- state -------------------------------------------------------------------------

    def conf(self):
        path = self.root / "bootstrap" / "bootstrap.conf"
        return pf_bootstrap.parse_bootstrap_conf(path.read_bytes(), label="conf")

    def bound(self):
        return Path(self.conf()["control_release"])

    def records(self):
        registry = pf_instance.load_registry(self.root)
        return {entry.instance_id: entry.record_path.read_bytes() for entry in registry.entries}

    def config_bytes(self):
        result = {}
        for slug, paths in self.paths.items():
            for name in ("pf-config.json", ".env"):
                path = paths["configuration"] / name
                result[f"{slug}/{name}"] = path.read_bytes() if path.exists() else None
        return result

    def assert_one_generation(self, release=None):
        """Exactly one selected generation: conf and every record name one release, which verifies, and every
        instance validates cleanly."""
        conf = self.conf()
        bound = Path(conf["control_release"])
        if release is not None:
            self.assertEqual(bound, Path(release))
        registry = pf_instance.load_registry(self.root)
        for entry, context, error in registry.records():
            self.assertIsNone(error)
            self.assertEqual(context.control.path, bound, entry.slug)
            self.assertEqual(context.control.sha256, conf["control_release_sha256"])
            validation = pf_instance.validate_context(context, running_release=bound)
            self.assertTrue(validation.mutation_allowed, validation.blocking_messages())
        checker = pf_bootstrap.PathChecker(self.root)
        pf_bootstrap.verify_release(checker, bound, expected_inventory_sha256=conf["control_release_sha256"],
                                    expected_release_id=bound.name)
        self.assertEqual(checker.blocking(), [])
        return bound

    def assert_release_verifies(self, release):
        _, sha = pf_bootstrap.load_control_inventory(release)
        checker = pf_bootstrap.PathChecker(self.root)
        pf_bootstrap.verify_release(checker, release, expected_inventory_sha256=sha, expected_release_id=release.name)
        self.assertEqual(checker.blocking(), [])

    def journal(self, operation_id=None):
        """The journal of ``operation_id``, else of the most recently written operation."""
        if operation_id is None:
            names = [name for name in op_dirs(self.root) if not name.startswith(".")]
            operation_id = max(names, key=lambda name: os.stat(
                str(self.root / "install-operations" / name / "journal.json")).st_mtime_ns)
        return pf_install.load_operation(self.root, operation_id)[2]

    # -- candidates ------------------------------------------------------------------------

    def candidate(self, name="candidate", **kwargs):
        if "mutate" not in kwargs:
            kwargs["mutate"] = {"compose.nas.yaml": lambda data: data + b"# PF-A2.1 test candidate " + name.encode() + b"\n"}
        return pfx.candidate_copy(self.base, name=name, **kwargs)

    def release_id(self, source):
        return pf_install.read_candidate(source).release_id

    # -- entry points ----------------------------------------------------------------------

    def run_pf(self, arguments, answers=(), *, unattended=False, trusted_launch=True, running_release=None):
        """In-process pf-admin main(), running as the release the root binds now."""
        stdout, stderr = io.StringIO(), io.StringIO()
        script = Script(answers, stdout)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                mock.patch.object(pf, "unattended", return_value=unattended), \
                mock.patch.object(pf, "input_line", script):
            code = pf.main(arguments, installation_root=self.root,
                           running_release=running_release or self.bound(), trusted_launch=trusted_launch)
        return code, stdout.getvalue(), stderr.getvalue()

    def cli(self, arguments, answers=(), *, executable=None, timeout=300, terminal=True):
        """The installed CLI (``<root>/bootstrap/pf``) with a pty and typed answers."""
        command = [str(executable or self.layout.launcher), *arguments]
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        with (pfx.typed_terminal(answers) if terminal else contextlib.nullcontext(subprocess.DEVNULL)) as stdin:
            return subprocess.run(command, env=environment, cwd=str(self.base), stdin=stdin, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, check=False, timeout=timeout)

    def control_request(self, source=None, release=None):
        return {"source": str(source)} if source is not None else {"release": release}

    def operation(self, kind, request, *, answers_interaction=None):
        """Preflight + execute in-process (no prompts); returns the final journal."""
        runner = pf_install._installed_runner(self.root, request.get("docker_endpoint"))
        result = pf_install._preflight(self.root, kind, request, runner=runner, running_release=self.bound())
        self.assertEqual(result.conflicts, [])
        return pf_install.execute(self.root, result.plan, runner=runner, interaction=answers_interaction or silent(),
                                  candidate=result.candidate, running_release=self.bound(), request=request)

    def forked(self, seam, when, index, body):
        """Run ``body`` in a forked child with a crash seam; returns the child's exit status (137 = crashed)."""
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            code = 0
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                with pfx.forked_crash(seam, when, index):
                    body()
            except BaseException:  # noqa: B902 - the child reports any failure as exit 1
                code = 1
            os._exit(code)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)

    def open_operation(self):
        items = [item for item in pf_install.operations(self.root) if item["phase"] in pf_install.OPEN_PHASES]
        return items[0] if items else None

    def resume_cli(self, *, abandon=False):
        item = self.open_operation()
        answers = []
        if item is not None:
            answers = [("ABANDON " if abandon and item["journal"] and pf_install._Run(
                self.root, item["plan"], "", item["journal"], base=self.root, runner=None, interaction=None).direction()
                == "forward" else "RESUME ") + item["operation_id"][-8:]]
        arguments = ["install", "resume"] + (["--abandon"] if abandon else [])
        return self.cli(arguments, answers)


# ============================================================================ SC-S1: schema


@ROOT_REQUIRED
class Schema(InstallBase):
    instances = ("a",)

    def test_sc_s1_contract_equals_the_embedded_schema_and_the_validator_is_strict(self):
        contract = PACKAGE / "contracts" / "install-operation.schema.json"
        self.assertEqual(json.loads(contract.read_text(encoding="utf-8")), pf_install.INSTALL_OPERATION_SCHEMA)
        journal = self.operation("control", self.control_request(self.candidate()))
        plan = pf_install.load_operation(self.root, journal["operation_id"])[0]
        self.assertEqual(pf_install.validate_document(plan, "plan"), [])
        self.assertEqual(pf_install.validate_document(journal, "journal"), [])
        bad = {
            "unknown key": ("plan", dict(plan, extra=1)),
            "missing key": ("plan", {key: value for key, value in plan.items() if key != "bindings"}),
            "boolean as integer": ("journal", dict(journal, sequence=True)),
            "unknown phase": ("journal", dict(journal, phase="half-done")),
            "nullable target": ("plan", dict(plan, candidate=dict(plan["candidate"], source_root="relative"))),
            "array items": ("journal", dict(journal, effects=[dict(journal["effects"][0], state="maybe")])),
            "map values": ("plan", dict(plan, candidate=dict(plan["candidate"], files={"x": "ABC"}))),
        }
        for label, (name, document) in bad.items():
            with self.subTest(case=label):
                self.assertTrue(pf_install.validate_document(document, name))
        path = self.root / "install-operations" / journal["operation_id"] / "journal.json"
        original = path.read_bytes()
        path.write_bytes(b'{"schema_version": 1, ' + original[1:])
        with self.assertRaises(pf_install.InstallError) as caught:
            pf_install.load_operation(self.root, journal["operation_id"])
        self.assertIn("Duplicate JSON object key", str(caught.exception))
        path.write_bytes(original)


# ============================================================================ CS: candidate source


@ROOT_REQUIRED
class CandidateSource(InstallBase):
    instances = ("a",)

    def test_cs1_a_complete_repo_copy_is_content_addressed(self):
        source = pfx.candidate_copy(self.base)
        first, second = pf_install.read_candidate(source), pf_install.read_candidate(source)
        self.assertEqual(first.release_id, second.release_id)
        self.assertEqual(first.release_id, pfx.content_release_id())
        self.assertRegex(first.release_id, r"\Ar-[0-9a-f]{16}\Z")
        self.assertEqual(first.inventory_bytes, pf_instance.build_control_inventory(first.release_id,
                                                                                    first.release_files))
        self.assertEqual(first.inventory_sha256, pf_instance.sha256_bytes(first.inventory_bytes))
        self.assertEqual(set(first.release_files), set(pf_install.CONTROL_RELEASE_FILES))
        self.assertEqual(set(first.bootstrap_files), set(pf_install.BOOTSTRAP_FILES))
        self.assertEqual((first.install_contract, first.install_schema_version), (1, 1))
        self.assertEqual((first.version, first.checkpoint), (pf.VERSION, pf.CHECKPOINT))

    def test_cs2_every_source_conflict_is_reported_together(self):
        source = pfx.candidate_copy(self.base, mutate={
            "deploy/synology/pf_docker.py": None,
            "deploy/synology/nas.env.example": b"#" * (pf_install.SOURCE_FILE_LIMIT + 1),
            "deploy/synology/pf_config.py": lambda data: data + b"\nmatch 1:\n    case 1:\n        pass\n",
        })
        link = source / "deploy/synology/pf_source.py"
        link.unlink()
        os.symlink(str(REPO_PACKAGE / "deploy/synology/pf_source.py"), str(link))
        with self.assertRaises(pf_install.PreflightRefused) as caught:
            pf_install.read_candidate(source)
        codes = {item.code: item.subject for item in caught.exception.conflicts}
        self.assertEqual(set(codes), {"source-missing", "source-unreadable", "source-too-large", "source-syntax"})
        self.assertTrue(codes["source-missing"].endswith("pf_docker.py"))
        self.assertTrue(codes["source-unreadable"].endswith("pf_source.py"))
        self.assertTrue(codes["source-too-large"].endswith("nas.env.example"))
        self.assertTrue(codes["source-syntax"].endswith("pf_config.py"))

    def test_cs3_candidate_code_runs_only_in_the_smoke_after_the_trust_decision(self):
        marker_line = b"open('cs3-marker', 'w').write('imported')\n"
        source = self.candidate(mutate={"deploy/synology/pf-admin.py": lambda data: data.replace(
            b"from __future__ import annotations\n", b"from __future__ import annotations\n" + marker_line, 1)})
        rid = self.release_id(source)
        before = snapshot(self.base)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["not the phrase"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("install-cancelled: Cancelled at summary; nothing was changed.", err)
        self.assertEqual(snapshot(self.base), before)
        self.assertEqual(list(self.base.rglob("cs3-marker")), [])
        self.assertFalse(Path("cs3-marker").exists())
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 0, out + err)
        operation = self.journal()["operation_id"]
        markers = list(self.base.rglob("cs3-marker"))
        self.assertEqual(markers, [self.root / "install-operations" / operation / "smoke-cwd" / "cs3-marker"])
        self.assert_release_verifies(self.root / "releases" / rid)
        self.assert_one_generation(self.root / "releases" / rid)
        # A candidate that writes into its own release directory fails the post-smoke re-verification.
        bad_line = b"open(__import__('os').path.join(__import__('os').path.dirname(__file__), 'planted'), 'w').write('x')\n"
        bad = self.candidate("bad", mutate={"deploy/synology/pf-admin.py": lambda data: data.replace(
            b"from __future__ import annotations\n", b"from __future__ import annotations\n" + bad_line, 1)})
        bad_id = self.release_id(bad)
        records = self.records()
        code, out, err = self.run_pf(["install", "control", "--source", str(bad)], ["INSTALL CONTROL " + bad_id])
        self.assertEqual(code, 1, out + err)
        self.assertIn("install-smoke-failed:", err)
        self.assertFalse((self.root / "releases" / bad_id).exists())
        self.assertFalse((self.root / "install-operations" / self.journal()["operation_id"] / "release.staging").exists())
        self.assertEqual(self.journal()["phase"], "cancelled")
        self.assertEqual(self.records(), records)
        self.assert_one_generation(self.root / "releases" / rid)


# ============================================================================ helpers shared by the classes below


def migrate_arguments(home, slug="legacy", *, launcher=None, endpoint=None, layout=None):
    arguments = ["install", "migrate-legacy", "--legacy-home", str(home["home"]), "--workspace",
                 str(home["workspace"]), "--slug", slug]
    arguments += ["--launcher-path", str(launcher)] if launcher is not None else ["--no-launcher"]
    arguments += ["--docker-endpoint", endpoint or layout.daemon_endpoint]
    return arguments


def register_arguments(layout, slug, paths, project):
    return ["install", "register", "--slug", slug, "--project", project, "--workspace", str(paths["workspace"]),
            "--configuration", str(paths["configuration"]), "--backups", str(paths["backups"]), "--recovery",
            str(paths["recovery"]), "--docker-endpoint", layout.daemon_endpoint]


def conflict_codes(text):
    codes = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            codes.append(line[2:].split(":", 1)[0])
    return codes


def fabricate_release(root, files):
    """A retained release written like the installer would (content-addressed id, protected modes)."""
    hashes = {name: pf_instance.sha256_bytes(data) for name, data in files.items()}
    release_id = pf_install.release_id_for(pf_install.content_digest(hashes))
    directory = Path(root) / "releases" / release_id
    pf_instance._create_private_dir(directory, 0o700)
    for name, data in files.items():
        pf_instance._write_private_file(directory / name, data, 0o600)
    pf_instance._write_private_file(directory / pf_bootstrap.CONTROL_INVENTORY_NAME,
                                    pf_instance.build_control_inventory(release_id, files), 0o600)
    return release_id


def forge_operation(root, template_plan, template_journal, *, kind, phase):
    """An open operation of ``kind`` in ``phase``: a schema-valid plan/journal pair (gate and preflight tests)."""
    operation_id = pf_install.new_operation_id()
    plan = dict(template_plan, operation_id=operation_id, kind=kind)
    plan_bytes = pf_instance.normalize_json(plan)
    journal = dict(template_journal, operation_id=operation_id, kind=kind, plan_sha256=pf_instance.sha256_bytes(plan_bytes),
                   phase=phase, result=None, next=["install resume"])
    directory = Path(root) / "install-operations" / operation_id
    pf_instance._create_private_dir(directory, 0o700)
    pf_instance._write_private_file(directory / "plan.json", plan_bytes, 0o600)
    pf_instance._write_private_file(directory / "journal.json", pf_instance.normalize_json(journal), 0o600)
    return operation_id


# ============================================================================ PF: preflight (A2-T01)


@ROOT_REQUIRED
class Preflight(InstallBase):

    def migrate_snapshot(self, home, launcher):
        return snapshot(self.root, home["home"], launcher.parent)

    def test_pf1_both_legacy_conflicts_in_one_run_and_nothing_moves(self):
        launcher = self.base / "bin" / "pf"
        launcher.parent.mkdir()
        os.chmod(launcher.parent, 0o755)
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, env_config=pfx.ENV_TEXT.replace("abc123", "other1"),
                               admin_legacy={"branch": "legacy"}, admin_config={})
        conf = (self.root / "bootstrap" / "bootstrap.conf").read_bytes()
        before = self.migrate_snapshot(home, launcher)
        code, out, err = self.run_pf(migrate_arguments(home, launcher=launcher, layout=self.layout))
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-preflight-refused: Preflight found", err)
        workspace, config = home["workspace"], home["config"]
        self.assertIn(f"  - legacy-env-conflict: {config}/.env: {workspace}/.env and {config}/.env both exist and differ. "
                      f"Neither is chosen automatically: compare them, keep the correct one at {config}/.env, move the "
                      "other out of both locations, then run the command again.", err)
        self.assertIn(f"  - legacy-admin-config-conflict: {config}/pf-config.json: {workspace}/deploy/synology/"
                      f"pf-config.json and {config}/pf-config.json both exist and differ.", err)
        self.assertIn("Resolve every item, then run the same command again.", err)
        self.assertEqual(self.migrate_snapshot(home, launcher), before)
        self.assertEqual((self.root / "bootstrap" / "bootstrap.conf").read_bytes(), conf)
        self.assertEqual(op_dirs(self.root), [])
        self.assertFalse(launcher.exists())
        record_evidence("cli/PF-1", {"exit": code, "stdout": out, "stderr": err})

    def test_pf2_every_conflict_of_a_bad_home_is_in_one_report(self):
        for variant in ("pending", "no-state"):
            with self.subTest(variant=variant):
                home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, env_config="POSTGRES_USER=x\n",
                                       admin_legacy={"branch": "legacy"},
                                       admin_config={"backup_read_group": "pf-no-such-group"},
                                       pending=variant == "pending", state=variant == "pending", name="home-" + variant)
                os.chmod(home["backups"], 0o770)
                before = snapshot(self.root, home["home"])
                code, out, err = self.run_pf(migrate_arguments(home, slug="a", layout=self.layout))
                self.assertEqual(code, 1, out + err)
                codes = set(conflict_codes(err))
                expected = {"legacy-env-conflict", "legacy-admin-config-conflict", "group-missing", "storage-replaceable",
                            "slug-taken", "legacy-pending-operation" if variant == "pending" else "legacy-state-missing"}
                self.assertTrue(expected <= codes, (expected - codes, err))
                self.assertEqual(snapshot(self.root, home["home"]), before)
                self.assertEqual(op_dirs(self.root), [])

    def test_pf3_control_refuses_paused_journals_and_unresolved_effects(self):
        pf.write_json(self.contexts["a"].journal_path, {"operation": "update", "phase": "paused"})
        effects = self.contexts["b"].operations_dir / "20261006T000000Z-backup-00000000"
        effects.mkdir(mode=0o700)
        (effects / "unresolved-effects.json").write_text('[{"id": "x", "outcome": "interrupted"}]\n')
        before = snapshot(self.root)
        code, out, err = self.run_pf(["install", "control", "--source", str(self.candidate())])
        self.assertEqual(code, 1, out + err)
        self.assertEqual(set(conflict_codes(err)) & {"instance-operation-pending", "instance-effects-unresolved"},
                         {"instance-operation-pending", "instance-effects-unresolved"})
        self.assertEqual(snapshot(self.root), before)

    def test_pf4_a_changed_launcher_or_verifier_is_refused(self):
        source = self.candidate(mutate={"pf.sh": lambda data: data + b"# changed launcher\n",
                                        "deploy/synology/pf_bootstrap.py": lambda data: data + b"# changed verifier\n"})
        before = snapshot(self.root)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)])
        self.assertEqual(code, 1, out + err)
        self.assertEqual(conflict_codes(err).count("bootstrap-change-unsupported"), 2, err)
        self.assertIn("changing the launcher or verifier is a launcher migration (PF-A4.3)", err)
        self.assertEqual(snapshot(self.root), before)

    def test_pf5_an_identical_candidate_is_a_no_op(self):
        before = snapshot(self.root)
        code, out, err = self.run_pf(["install", "control", "--source", str(pfx.candidate_copy(self.base))])
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Release {self.old_release.name} is already the bound control; nothing was changed.", out)
        self.assertEqual(snapshot(self.root), before)

    def test_pf6_free_space_below_the_margin(self):
        small = os.statvfs_result((4096, 4096, 100, 10, 10, 100, 10, 10, 0, 255))
        with mock.patch.object(pf_install.os, "statvfs", return_value=small):
            code, out, err = self.run_pf(["install", "control", "--source", str(self.candidate())])
        self.assertEqual(code, 1, out + err)
        self.assertIn("free-space", conflict_codes(err))

    def test_pf7_daemon_answers_refuse_registration_and_only_docker_info_runs(self):
        cases = (("rootless", {"info": pfx.daemon_info(rootless=True)}, "pf-c", "daemon-rootless"),
                 ("same engine and project", {}, "pf-a", "project-taken"),
                 ("unreachable", {"info_exit": 1}, "pf-c", "daemon-unreachable"))
        for label, state, project, code_expected in cases:
            with self.subTest(case=label):
                paths = pfx.data_home(self.base / ("c-" + label.replace(" ", "-")), project=project, group=GROUP)
                self.fake.write_state(dict(pfx.default_docker_state(), **state))
                self.fake.clear_calls()
                before = snapshot(self.root)
                code, out, err = self.run_pf(register_arguments(self.layout, "c", paths, project))
                self.assertEqual(code, 1, out + err)
                self.assertIn(code_expected, conflict_codes(err))
                self.assertEqual(self.fake.argvs() and set(map(tuple, self.fake.argvs())), {tuple(PROBE)})
                self.assertEqual(snapshot(self.root), before)

    def test_pf8_init_refuses_a_non_empty_non_canonical_or_untrusted_root(self):
        full = self.base / "full"
        full.mkdir()
        (full / "x").write_text("x")
        untrusted = self.base / "open"
        untrusted.mkdir()
        os.chmod(untrusted, 0o777)
        for root, code in ((full, "root-exists"), ("//x", "root-noncanonical"), (untrusted / "root", "root-untrusted")):
            with self.subTest(root=str(root)):
                result = pf_install._preflight(None, "init", {"root": str(root), "source_root": str(REPO_PACKAGE),
                                                              "interpreter": PY, "tools": {}, "no_launcher": True},
                                               runner=None, running_release=None)
                self.assertIn(code, [item.code for item in result.conflicts])

    def test_pf9_any_open_operation_refuses_every_new_operation(self):
        journal = self.operation("control", self.control_request(self.candidate()))
        plan = pf_install.load_operation(self.root, journal["operation_id"])[0]
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        paths = pfx.data_home(self.base / "c", project="pf-c", group=GROUP)
        for kind in pf_install.KINDS:
            for phase in ("planned", "needs_operator"):
                with self.subTest(kind=kind, phase=phase):
                    forged = forge_operation(self.root, plan, journal, kind=kind, phase=phase)
                    for arguments in (["install", "control", "--source", str(pfx.candidate_copy(self.base, name="same"))],
                                      ["install", "control", "--release", self.bound().name],
                                      register_arguments(self.layout, "c", paths, "pf-c"),
                                      migrate_arguments(home, layout=self.layout)):
                        code, out, err = self.run_pf(arguments)
                        self.assertEqual(code, 1, (arguments, out + err))
                        self.assertIn("install-operation-pending", conflict_codes(err), err)
                    shutil.rmtree(str(self.root / "install-operations" / forged))
                    shutil.rmtree(str(self.base / "same"))


# ============================================================================ RI: repository installer


class InitBase(InstallBase):
    """A protected empty parent for a new root, an isolated launcher directory and the repository release id."""

    instances = ()

    def setUp(self):
        super().setUp()
        self.parent = self.base / "parent"
        self.parent.mkdir()
        os.chmod(self.parent, 0o755)
        self.new_root = self.parent / "install"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        os.chmod(self.bin, 0o755)
        self.launcher_path = self.bin / "pf"
        self.rid = pfx.content_release_id()

    def init(self, *extra, answers=None, launcher=True):
        arguments = ["init", "--root", str(self.new_root), "--interpreter", PY]
        arguments += ["--launcher-path", str(self.launcher_path)] if launcher else ["--no-launcher"]
        return pfx.run_installer(arguments + list(extra),
                                 answers=["INSTALL CONTROL " + self.rid] if answers is None else answers)

    def run_init(self, arguments, answers):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = pf_install.run_init(arguments, interaction(answers, stdout), source_root=REPO_PACKAGE)
        return code, stdout.getvalue(), stderr.getvalue()

    def crash_init(self, seam, index, when="after", source_root=REPO_PACKAGE):
        def body():
            pf_install.run_init(["--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                                interaction(["INSTALL CONTROL " + self.rid]), source_root=source_root)
        return self.forked(seam, when, index, body)

    def leftovers(self):
        return sorted(name for name in os.listdir(self.parent) if name.startswith(".install.init-"))


@ROOT_REQUIRED
class RepositoryInstaller(InitBase):

    def test_ri1_init_end_to_end(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        operations = op_dirs(self.new_root)
        self.assertEqual(len(operations), 1)
        plan, _, journal = pf_install.load_operation(self.new_root, operations[0])
        self.assertEqual((plan["kind"], journal["phase"]), ("init", "completed"))
        self.assertEqual(pf_instance.validate_installation_root(self.new_root).blocking(), [])
        for name in pf_install.WRAPPER_NAMES:
            path = self.new_root / "bootstrap" / name
            self.assertEqual(path.read_bytes(), (PACKAGE / name).read_bytes())
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o700)
        self.assertEqual((self.new_root / "profiles" / pf_install.PROFILE_NAME).read_bytes(), pf_instance.normalize_json(
            {"schema_version": 1, "id": "partflow-staging-legacy", "version": pf.VERSION, "application": "partflow",
             "compose_file": "compose.nas.yaml"}))
        self.assertEqual((self.new_root / "policies" / "staging.json").read_bytes(),
                         pf_instance.normalize_json({"schema_version": 1, "revision": 1, "environment": "staging"}))
        self.assertEqual(sorted(os.listdir(self.new_root / "policies")), ["staging.json"])
        self.assertEqual(self.launcher_path.read_text(),
                         pf_install.LAUNCHER_TEMPLATE.format(root=self.new_root))
        self.assertEqual(stat.S_IMODE(os.lstat(self.launcher_path).st_mode), 0o755)
        self.assertEqual(journal["result"]["launcher"]["path"], str(self.launcher_path))
        listing = subprocess.run([str(self.launcher_path), "instances"], env={"PATH": "/usr/bin:/bin"},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 check=False, timeout=120)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("(no registrations)", listing.stdout)
        self.assertEqual([name for name in os.listdir(self.parent) if ".init-" in name], [])
        record_evidence("cli/RI-1", {"stdout": result.stdout, "stderr": result.stderr, "instances": listing.stdout})

    def test_ri2_refusals_create_nothing(self):
        before = snapshot(self.parent, self.bin)
        cases = (
            ("non-root", dict(preexec_fn=lambda: os.setuid(65534)), ["init", "--root", str(self.new_root)],
             "Run as root: sudo sh ./deploy/synology/install-control.sh init --root <root>"),
            ("no terminal", dict(terminal=False), ["init", "--root", str(self.new_root)],
             "Interactive terminal required; installation has no --yes bypass."),
            ("installed verb", {}, ["register", "--slug", "x"],
             "installer-verb-installed-only: install-control.sh only initializes a new installation root. Run "
             "'register' from the installed control: sudo <root>/bootstrap/pf install register …. Nothing was read or "
             "changed."),
        )
        for label, options, arguments, copy_text in cases:
            with self.subTest(case=label):
                result = pfx.run_installer(arguments, **options)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(copy_text, result.stderr)
                self.assertEqual(snapshot(self.parent, self.bin), before)

    def test_ri3_own_leftovers_are_removed_and_a_foreign_one_refuses(self):
        aside = self.base / "aside"
        self.assertEqual(self.crash_init("_apply_effect", 0), 137)       # after build-root: intent + build
        first = self.leftovers()
        self.assertEqual(len(first), 1)
        os.rename(self.parent / first[0], aside)  # kept aside: the next init would remove it as its own
        self.assertEqual(self.crash_init("_journal_write", 0), 137)      # plan.json only: an effect-free .new intent
        os.rename(aside, self.parent / first[0])
        own = self.leftovers()
        self.assertEqual(len(own), 2)
        second = next(name for name in own if name != first[0])
        self.assertEqual([entry.endswith(".new") for entry in os.listdir(self.parent / second / "install-operations")],
                         [True])
        self.assertFalse(self.new_root.exists())
        foreign = self.parent / ".install.init-foreign1"
        foreign.mkdir()
        (foreign / "data").write_text("not ours")
        result = self.init(launcher=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"  - init-leftover-unknown: {foreign}:", result.stderr)
        self.assertEqual(sorted(name for name in os.listdir(self.parent) if name.startswith(".install.init-")),
                         sorted(own + [foreign.name]))
        shutil.rmtree(str(foreign))
        result = self.init(launcher=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("Note: init-leftover-removed:"), 2)
        self.assertEqual(sorted(os.listdir(self.parent)), ["install"])
        self.assertEqual(pf_instance.validate_installation_root(self.new_root).blocking(), [])

    def test_ri4_an_existing_launcher_is_left_unchanged(self):
        variants = {
            "legacy": '#!/bin/sh\n# PartFlow NAS installed launcher\nexec "/volume1/partflow/control/pf.sh" "$@"\n',
            "foreign": "#!/bin/sh\necho someone else\n",
            "installed": pf_install.LAUNCHER_TEMPLATE.format(root="/srv/other-root"),
        }
        for kind, text in variants.items():
            with self.subTest(kind=kind):
                if self.new_root.exists():
                    shutil.rmtree(str(self.new_root))
                self.launcher_path.write_text(text)
                before = snapshot(self.bin)
                result = self.init()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(pf_install.classify_launcher(self.launcher_path).kind, kind)
                self.assertEqual(snapshot(self.bin), before)
                self.assertIn(f"Note: launcher-left: {self.launcher_path} exists ({kind})", result.stdout)
                self.assertIn(f"Global launcher {self.launcher_path}: left unchanged (use {self.new_root}/bootstrap/pf)",
                              result.stdout)
                self.assertIn(f"'sudo {self.new_root}/bootstrap/pf install register'", result.stdout)


# ============================================================================ LB: launcher


@ROOT_REQUIRED
class LauncherBinding(InitBase):

    def test_lb1_classification(self):
        path = self.bin / "probe"
        self.assertEqual(pf_install.classify_launcher(path).kind, "absent")
        path.write_text('#!/bin/sh\n# PartFlow NAS installed launcher\nexec "/volume1/home/control/pf.sh" "$@"\n')
        state = pf_install.classify_launcher(path)
        self.assertEqual((state.kind, state.target), ("legacy", "/volume1/home/control"))
        path.write_bytes(pf_install.render_launcher(self.new_root))
        state = pf_install.classify_launcher(path)
        self.assertEqual((state.kind, state.target), ("installed", str(self.new_root)))
        path.write_text(pf_install.LAUNCHER_TEMPLATE.format(root=self.new_root) + "# extra\n")
        self.assertEqual(pf_install.classify_launcher(path).kind, "foreign")
        link = self.bin / "link"
        path.write_bytes(pf_install.render_launcher(self.new_root))
        os.symlink(str(path), str(link))
        self.assertEqual(pf_install.classify_launcher(link).kind, "foreign")

    def test_lb2_global_launcher_execs_the_bootstrap(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        endpoint, _ = pfx.install_daemon_socket(self.base)
        paths = pfx.data_home(self.base / "lb2", project="pf-lb2", group=GROUP)
        conf = pf_bootstrap.parse_bootstrap_conf((self.new_root / "bootstrap/bootstrap.conf").read_bytes(), label="c")
        pf_instance.register_instance(self.new_root, {
            "slug": "a", "compose_project": "pf-lb2", "approved_environment": "staging",
            "daemon": {"endpoint": endpoint, "engine_id": pfx.ENGINE_ID}, "paths": paths,
            "control_release_id": Path(conf["control_release"]).name,
            "profile_path": self.new_root / "profiles" / pf_install.PROFILE_NAME,
            "policy_path": self.new_root / "policies" / "staging.json"})
        status = subprocess.run([str(self.launcher_path), "--instance", "a", "status"], env={"PATH": "/usr/bin:/bin"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                check=False, timeout=120)
        self.assertIn("Instance: a (", status.stdout, status.stderr)
        self.assertIn(f"Control: bound {self.rid}", status.stdout)
        self.assertIn("Protected context: trusted (mutation allowed)", status.stdout)

    def test_lb3_a_root_that_cannot_be_embedded_is_refused(self):
        for root in ("/srv/deploy admin", "/srv/deploy$admin", "/srv/a\"b", "//srv/x", "relative"):
            with self.subTest(root=root):
                with self.assertRaises(pf_install.InstallError):
                    pf_install.render_launcher(root)


# ============================================================================ LM: legacy migration


@ROOT_REQUIRED
class LegacyMigration(InstallBase):
    instances = ()

    def setUp(self):
        super().setUp()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        os.chmod(self.bin, 0o755)

    def migrate(self, home, slug="legacy", launcher=None):
        arguments = migrate_arguments(home, slug, launcher=launcher, layout=self.layout)
        return self.run_pf(arguments, ["MIGRATE " + slug])

    def test_lm1_copies_published_registered_with_the_users_paths_and_v25_stays_in_control(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={}, launcher=None)
        legacy_env = home["workspace"] / ".env"
        legacy_config = home["workspace"] / "deploy/synology/pf-config.json"
        sources = {path: (path.read_bytes(), os.lstat(path)) for path in (legacy_env, legacy_config)}
        code, out, err = self.migrate(home, launcher=self.bin / "pf")
        self.assertEqual(code, 0, out + err)
        for source, target in ((legacy_env, home["config"] / ".env"),
                               (legacy_config, home["config"] / "pf-config.json")):
            data, info = sources[source]
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(os.lstat(target).st_mode), stat.S_IMODE(info.st_mode))
            self.assertEqual(os.lstat(target).st_gid, info.st_gid)
            self.assertEqual(source.read_bytes(), data)  # the legacy copy is retained
        self.assertEqual([name for name in os.listdir(home["config"]) if ".pf-migrate-" in name], [])
        context = pf_instance.resolve_instance(pf_instance.load_registry(self.root), instance="legacy")
        self.assertEqual(context.paths.workspace, home["workspace"])
        self.assertEqual(context.compose_project, "partflow-legacy")
        self.assertEqual(context.paths.configuration, home["config"])
        code, out, err = self.run_pf(["instances"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"LEGACY CONTROL ACTIVE {home['control']}: mutating commands refused (legacy-control-active)", out)
        code, out, err = self.run_pf(["--instance", "legacy", "backup"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: legacy-control-active: Instance legacy was migrated from v2.5 (operation", err)
        self.assertEqual(self.launcher_text(), pf_install.LAUNCHER_TEMPLATE.format(root=self.root))
        # The declared adoption limit: without the v2.5 control the A1.3 ownership gate refuses unlabeled resources.
        os.rename(home["control"], home["home"] / "control.retired-by-test")
        legacy = pfx.container("c" * 64, "partflow-legacy-db-1",
                               {pf.pf_docker.COMPOSE_PROJECT_LABEL: "partflow-legacy",
                                pf.pf_docker.COMPOSE_SERVICE_LABEL: "db"})
        self.fake.write_state(dict(pfx.default_docker_state(), containers=[legacy]))
        code, out, err = self.run_pf(["--instance", "legacy", "backup"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("resource-legacy-unlabeled", err)

    def launcher_text(self):
        return (self.bin / "pf").read_text()

    def test_lm2_legacy_state_is_reported_not_imported(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={},
                               state_files={"deployed.json": '{"sha": "' + "1" * 40 + '"}\n'})
        code, out, err = self.migrate(home)
        self.assertEqual(code, 0, out + err)
        self.assertIn("Note: legacy-state-not-imported: 1 legacy state file(s)", out)
        plan = pf_install.load_operation(self.root, op_dirs(self.root)[0])[0]
        data = (home["state"] / "deployed.json").read_bytes()
        self.assertEqual(plan["legacy"]["state_report"], [{"name": "deployed.json", "size": len(data),
                                                           "sha256": pf_instance.sha256_bytes(data)}])
        context = pf_instance.resolve_instance(pf_instance.load_registry(self.root), instance="legacy")
        self.assertFalse((context.state_dir / "deployed.json").exists())
        self.assertEqual(sorted(os.listdir(home["state"])), ["deployed.json", "operation.lock"])

    def test_lm3_a_legacy_launcher_is_left_unchanged(self):
        other = pfx.legacy_home(self.base, name="otherhome", env_repo=pfx.ENV_TEXT, admin_legacy={},
                                project="partflow-other", launcher=self.bin / "pf-other")
        for label, home, launcher in (("this home", None, self.bin / "pf"), ("another home", other, self.bin / "pf-other")):
            with self.subTest(case=label):
                if home is None:
                    home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={}, launcher=launcher)
                before = launcher.read_bytes()
                code, out, err = self.migrate(home, slug="s-" + home["project"][-5:], launcher=launcher)
                self.assertEqual(code, 0, out + err)
                self.assertEqual(launcher.read_bytes(), before)
                self.assertIn(f"Global launcher {launcher}: left unchanged", out)


# ============================================================================ SC: select a retained release


@ROOT_REQUIRED
class SelectControl(InstallBase):

    def install(self, source):
        rid = self.release_id(source)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 0, out + err)
        return rid

    def select(self, release_id, phrase=None):
        return self.run_pf(["install", "control", "--release", release_id], [phrase or "SELECT CONTROL " + release_id])

    def test_sc1_select_the_old_release_then_the_new_one_again(self):
        source = self.candidate()
        new = self.install(source)
        self.assert_one_generation(self.root / "releases" / new)
        code, out, err = self.select(self.old_release.name)
        self.assertEqual(code, 0, out + err)
        journal = self.journal()
        self.assertEqual(journal["phase"], "completed")
        self.assertTrue((self.root / "install-operations" / journal["operation_id"] / "smoke.json").exists())
        self.assert_one_generation(self.old_release)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["SELECT CONTROL " + new])
        self.assertEqual(code, 0, out + err)
        self.assertIn("The release is already published; it is selected again after the same smoke and checks.", out)
        plan = pf_install.load_operation(self.root, self.journal()["operation_id"])[0]
        self.assertTrue(plan["selected_existing_release"])
        self.assertEqual([effect for effect, _ in pf_install._planned_effects(plan)][:2], ["smoke", "pre-bind-verify"])
        self.assert_one_generation(self.root / "releases" / new)
        self.assert_release_verifies(self.old_release)

    def test_sc2_missing_tampered_bound_incompatible_and_bootstrap_changing_releases(self):
        new = self.install(self.candidate())
        cases = []
        cases.append(("missing", "r-0000000000000000", "release-not-retained"))
        files = {name: (self.old_release / name).read_bytes() for name in pfx.release_files()}
        contract = dict(files, **{"pf_install.py": files["pf_install.py"].replace(b"INSTALL_CONTRACT = 1",
                                                                                  b"INSTALL_CONTRACT = 2", 1)})
        cases.append(("contract", fabricate_release(self.root, contract), "install-contract-incompatible"))
        verifier = dict(files, **{"pf_bootstrap.py": files["pf_bootstrap.py"] + b"# other verifier\n"})
        cases.append(("bootstrap", fabricate_release(self.root, verifier), "bootstrap-change-unsupported"))
        tampered = self.old_release / "nas.env.example"
        os.chmod(tampered, 0o600)
        with open(str(tampered), "ab") as stream:
            stream.write(b"# tampered\n")
        cases.append(("tampered", self.old_release.name, "release-not-retained"))
        for label, release_id, expected in cases:
            with self.subTest(case=label):
                before = snapshot(self.root)
                code, out, err = self.select(release_id)
                self.assertEqual(code, 1, out + err)
                self.assertIn(expected, conflict_codes(err), err)
                self.assertEqual(snapshot(self.root), before)
        code, out, err = self.select(new)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Release {new} is already the bound control; nothing was changed.", out)

    def test_sc3_a_published_unbound_source_is_selected_and_a_corrupt_one_collides(self):
        source = self.candidate()
        new = self.install(source)
        code, out, err = self.select(self.old_release.name)
        self.assertEqual(code, 0, out + err)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["SELECT CONTROL " + new])
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.root / "install-operations" / self.journal()["operation_id"] / "smoke.json").exists())
        self.assert_one_generation(self.root / "releases" / new)
        code, out, err = self.select(self.old_release.name)
        self.assertEqual(code, 0, out + err)
        planted = self.root / "releases" / new / "planted.txt"
        pf_instance._write_private_file(planted, b"x", 0o600)
        before = snapshot(self.root)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)])
        self.assertEqual(code, 1, out + err)
        self.assertIn("release-id-collision", conflict_codes(err))
        self.assertEqual(snapshot(self.root), before)


# ============================================================================ SM: smoke checks live configuration


@ROOT_REQUIRED
class SmokeConfig(InstallBase):

    def test_sm1_a_candidate_that_rejects_a_live_config_is_not_activated(self):
        config = self.paths["a"]["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), ci_workflow="ci.yml")) + "\n")
        source = self.candidate(mutate={"deploy/synology/pf_config.py": lambda data: data.replace(
            b'    "ci_workflow": "ci.yml", "health_timeout_seconds": 180,\n',
            b'    "health_timeout_seconds": 180,\n', 1)})
        rid = self.release_id(source)
        records = self.records()
        conf = (self.root / "bootstrap/bootstrap.conf").read_bytes()
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 1, out + err)
        self.assertIn("install-smoke-failed:", err)
        self.assertIn("config of a rejected by the candidate (config)", err)
        self.assertFalse((self.root / "releases" / rid).exists())
        self.assertEqual(self.records(), records)
        self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)

    def test_sm2_an_already_invalid_config_is_a_note_not_a_regression(self):
        (self.paths["b"]["configuration"] / ".env").write_text("not a valid line\n")
        source = self.candidate()
        code, out, err = self.run_pf(["install", "control", "--source", str(source)],
                                     ["INSTALL CONTROL " + self.release_id(source)])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Note: config-baseline-invalid: instance b: pf-config.json ok, .env invalid", out)
        self.assert_one_generation(self.root / "releases" / self.release_id(source))


# ============================================================================ CN: cancellation (A2-T02)


def interrupt_effect(etype, *, after=False, effect_id=None):
    """Patch the effect seam so that ``etype`` (or ``effect_id``) raises KeyboardInterrupt before/after the real call."""
    real = pf_install._apply_effect

    def wrapper(run, current_id, current_type):
        selected = current_type == etype if effect_id is None else current_id == effect_id
        if selected and not after:
            raise KeyboardInterrupt
        result = real(run, current_id, current_type)
        if selected and after:
            raise KeyboardInterrupt
        return result

    return mock.patch.object(pf_install, "_apply_effect", wrapper)


def interrupt_runner(label):
    """KeyboardInterrupt from inside the runner while the child ``label`` (smoke/verify) would run."""
    real = pf_install.pf_runner.ProcessRunner.run

    def run(self, spec):
        if spec.label == label:
            raise KeyboardInterrupt
        return real(self, spec)

    return mock.patch.object(pf_install.pf_runner.ProcessRunner, "run", run)


def interrupt_write(directory_name, count):
    """KeyboardInterrupt on the ``count``-th private file write inside a directory named ``directory_name`` (mid-copy)."""
    real = pf_instance._write_private_file
    seen = [0]

    def write(path, data, mode=0o600):
        if Path(path).parent.name == directory_name:
            seen[0] += 1
            if seen[0] == count:
                raise KeyboardInterrupt
        return real(path, data, mode)

    return mock.patch.object(pf_instance, "_write_private_file", write)


@ROOT_REQUIRED
class Cancellation(InitBase):
    instances = ("a", "b")

    def test_cn1_init_cancels_at_every_stage_and_at_the_summary(self):
        before = snapshot(self.parent)
        transcripts = {}
        for stage, arguments, tokens in (
                ("root", ["--interpreter", PY, "--no-launcher"], ("q", "", EOFError, KeyboardInterrupt)),
                ("summary", ["--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                 ("wrong phrase", "q", "", EOFError, KeyboardInterrupt))):
            for token in tokens:
                with self.subTest(stage=stage, token=str(token)):
                    code, out, err = self.run_init(arguments, [token])
                    self.assertEqual(code, 1, out + err)
                    self.assertIn(f"ERROR: install-cancelled: Cancelled at {stage}; nothing was changed.", err)
                    self.assertEqual(snapshot(self.parent), before)
                    self.assertEqual(self.leftovers(), [])
                    transcripts[f"{stage}/{token if isinstance(token, str) else token.__name__}"] = {
                        "exit": code, "stdout": out, "stderr": err}
        record_evidence("cli/CN-1", transcripts)

    def test_cn2_installed_kinds_cancel_at_every_stage_and_at_the_summary(self):
        source = self.candidate()
        paths = pfx.data_home(self.base / "c", project="pf-c", group=GROUP)
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        launcher = self.bin / "pf"
        endpoint = self.layout.daemon_endpoint
        cases = {
            "control": (["install", "control"], [str(source)], ["candidate"]),
            "register": (["install", "register", "--docker-endpoint", endpoint],
                         ["c", "pf-c", str(paths["workspace"]), str(paths["configuration"]), str(paths["backups"]),
                          str(paths["recovery"])], list(pf_install.INPUT_STAGES["register"])),
            "migrate-legacy": (["install", "migrate-legacy", "--launcher-path", str(launcher), "--docker-endpoint",
                                endpoint], [str(home["home"]), str(home["workspace"]), "legacy"],
                               list(pf_install.INPUT_STAGES["migrate-legacy"])),
        }
        watched = (self.root, home["home"], self.bin, paths["configuration"])
        records = self.records()
        for kind, (arguments, valid, stages) in cases.items():
            for index, stage in enumerate(stages + ["summary"]):
                for token in ("q", "", EOFError, KeyboardInterrupt):
                    with self.subTest(kind=kind, stage=stage, token=str(token)):
                        before = snapshot(*watched)
                        code, out, err = self.run_pf(arguments, valid[:index] + [token])
                        self.assertEqual(code, 1, out + err)
                        self.assertIn(f"ERROR: install-cancelled: Cancelled at {stage}; nothing was changed.", err)
                        self.assertEqual(snapshot(*watched), before)
                        self.assertEqual(op_dirs(self.root), [])
                        self.assertEqual(self.records(), records)
                        self.assertIsNone(pf_instance.load_registry(self.root).default_instance_id)

    def test_cn3_an_interrupt_during_staging_or_smoke_cancels_and_removes_own_staging(self):
        records, conf = self.records(), (self.root / "bootstrap/bootstrap.conf").read_bytes()
        for label, patcher in (("stage-release", interrupt_write("release.staging", 3)),
                               ("smoke", interrupt_runner("smoke"))):
            with self.subTest(kind="control", effect=label):
                source = self.candidate("cn3-" + label)
                rid = self.release_id(source)
                with patcher:
                    code, out, err = self.run_pf(["install", "control", "--source", str(source)],
                                                 ["INSTALL CONTROL " + rid])
                self.assertEqual(code, 1, out + err)
                journal = self.journal()
                self.assertIn(f"ERROR: install-cancelled: Cancelled during {'planned' if label == 'stage-release' else 'prepared'}; "
                              f"the private staging of operation {journal['operation_id']} was removed and the "
                              "installed control is unchanged.", err)
                self.assertEqual(journal["phase"], "cancelled")
                self.assertFalse((self.root / "install-operations" / journal["operation_id"] / "release.staging").exists())
                self.assertFalse((self.root / "releases" / rid).exists())
                self.assertEqual(self.records(), records)
                self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)
        before = snapshot(self.parent)
        for label, patcher in (("build-root", interrupt_write("bootstrap", 2)), ("smoke", interrupt_runner("smoke"))):
            with self.subTest(kind="init", effect=label):
                with patcher:
                    code, out, err = self.run_init(["--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                                                   ["INSTALL CONTROL " + self.rid])
                self.assertEqual(code, 1, out + err)
                self.assertIn("ERROR: install-cancelled: Cancelled during", err)
                self.assertIn("the private build directory", err)
                self.assertFalse(self.new_root.exists())
                self.assertEqual(self.leftovers(), [])
                self.assertEqual(snapshot(self.parent), before)

    def test_cn4_migrate_interrupted_after_a_publication_removes_its_copies(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        legacy = snapshot(home["workspace"], home["control"])
        registry = (self.root / "registry/instances.json").read_bytes()
        with interrupt_effect(None, after=True, effect_id="publish-legacy-file:env"):
            code, out, err = self.run_pf(migrate_arguments(home, layout=self.layout), ["MIGRATE legacy"])
        self.assertEqual(code, 1, out + err)
        config = home["config"]
        self.assertIn(f"ERROR: install-cancelled: Cancelled during validated; the copied {config}/.env, "
                      f"{config}/pf-config.json were removed, the legacy files are unchanged and nothing was registered.",
                      err)
        self.assertEqual(self.journal()["phase"], "cancelled")
        self.assertEqual(sorted(os.listdir(config)), [])
        self.assertEqual(snapshot(home["workspace"], home["control"]), legacy)
        self.assertEqual((self.root / "registry/instances.json").read_bytes(), registry)
        self.assertEqual(pf_instance.pending_registrations(self.root), [])


# ============================================================================ CC: concurrency


def identities(fake):
    state = fake.state()
    return {
        "containers": sorted([item["id"], item["name"], item.get("status"), json.dumps(item.get("labels"), sort_keys=True)]
                             for item in state["containers"]),
        "volumes": sorted([item["name"], json.dumps(item.get("labels"), sort_keys=True)] for item in state["volumes"]),
        "networks": sorted([item["id"], item["name"]] for item in state["networks"]),
        "images": sorted([item["id"], sorted(item["repo_tags"])] for item in state["images"]),
    }


def wait_for(path, process=None, timeout=60):
    deadline = time.monotonic() + timeout
    while not Path(path).exists() and time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            break
        time.sleep(0.05)
    return Path(path).exists()


class BackgroundBase(InitBase):
    """InitBase plus a background installed-CLI (or installer) process with a typed terminal."""

    instances = ("a", "b")

    def popen_cli(self, arguments, answers, *, script=None):
        terminal = pfx.typed_terminal(answers)
        stdin = terminal.__enter__()
        out = open(str(self.base / "bg-out.txt"), "wb")
        err = open(str(self.base / "bg-err.txt"), "wb")
        command = (["sh", str(script)] if script else [str(self.layout.launcher)]) + list(arguments)
        process = subprocess.Popen(command, env={"PATH": "/usr/bin:/bin", "TERM": "dumb"}, cwd=str(self.base),
                                   stdin=stdin, stdout=out, stderr=err)

        def finish():
            try:
                return process.wait(timeout=180)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                out.close()
                err.close()
                terminal.__exit__(None, None, None)
        return process, finish


@ROOT_REQUIRED
class Concurrency(BackgroundBase):

    def test_cc1_a_held_instance_lock_makes_control_busy(self):
        handle = pf_instance.acquire_instance_lock(self.contexts["a"])
        try:
            before = snapshot(self.root)
            source = self.candidate()
            code, out, err = self.run_pf(["install", "control", "--source", str(source)],
                                         ["INSTALL CONTROL " + self.release_id(source)])
        finally:
            handle.release()
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: install-busy: Another operation holds {self.contexts['a'].lock_path}; nothing was changed.",
                      err)
        self.assertEqual(snapshot(self.root), before)

    def test_cc2_an_instance_route_is_busy_while_control_holds_the_locks(self):
        source = self.candidate(block_on="import")
        process, finish = self.popen_cli(["install", "control", "--source", str(source)],
                                         ["INSTALL CONTROL " + self.release_id(source)])
        try:
            self.assertTrue(wait_for(self.base / "candidate.reached", process), (self.base / "bg-err.txt").read_text())
            code, out, err = self.run_pf(["--instance", "a", "backup"])
            self.assertEqual(code, 1, out + err)
            self.assertIn("Another operation holds the lock of instance a; try again after it finishes.", err)
        finally:
            pfx.release_fifo(self.base)
            returncode = finish()
        self.assertEqual(returncode, 0, (self.base / "bg-err.txt").read_text())
        self.assert_one_generation(self.root / "releases" / self.release_id(source))

    def test_cc3_concurrent_registrations_one_is_busy_and_the_request_converges(self):
        paths = pfx.data_home(self.base / "c", project="pf-c", group=GROUP)
        arguments = register_arguments(self.layout, "c", paths, "pf-c")
        handle = pf_instance.acquire_registry_lock(self.root)
        try:
            before = snapshot(self.root)
            code, out, err = self.run_pf(arguments, ["REGISTER c"])
            self.assertEqual(code, 1, out + err)
            self.assertIn("ERROR: install-busy: Another operation holds", err)
            self.assertEqual(snapshot(self.root), before)
        finally:
            handle.release()
        code, out, err = self.run_pf(arguments, ["REGISTER c"])
        self.assertEqual(code, 0, out + err)
        registry = (self.root / "registry/instances.json").read_bytes()
        code, out, err = self.run_pf(arguments, ["REGISTER c"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("slug-taken", conflict_codes(err))
        self.assertEqual((self.root / "registry/instances.json").read_bytes(), registry)
        self.assertEqual([entry.slug for entry in pf_instance.load_registry(self.root).entries], ["a", "b", "c"])

    def controller(self, slug):
        context = pf_instance.resolve_instance(pf_instance.load_registry(self.root), instance=slug)
        validation = pf_instance.validate_context(context, running_release=self.bound())
        return pf.Controller(context, validation=validation, running_release=self.bound())

    def test_cc4_an_open_registration_gates_only_its_own_instance(self):
        paths = pfx.data_home(self.base / "c", project="pf-c", group=GROUP)
        request = {"slug": "c", "project": "pf-c", "workspace": str(paths["workspace"]),
                   "configuration": str(paths["configuration"]), "backups": str(paths["backups"]),
                   "recovery": str(paths["recovery"]), "docker_endpoint": self.layout.daemon_endpoint}
        self.assertEqual(self.forked("_apply_effect", "before", 1, lambda: self.operation("register", request)), 137)
        self.assertEqual(self.open_operation()["journal"]["phase"], "verifying")
        with contextlib.redirect_stdout(io.StringIO()), self.controller("a").lock("backup"):
            pass
        with self.assertRaises(pf.Failure) as caught:
            with contextlib.redirect_stdout(io.StringIO()), self.controller("c").lock("backup"):
                pass
        self.assertIn("install-operation-pending: Install operation", str(caught.exception))
        self.assertIn("backup was refused and nothing was changed", str(caught.exception))

    def test_cc5_a_binding_changed_after_validation_is_refused_inside_the_lock(self):
        controller = self.controller("a")
        self.operation("control", self.control_request(self.candidate()))
        before = sorted(os.listdir(self.contexts["a"].operations_dir))
        with self.assertRaises(pf.Failure) as caught:
            with contextlib.redirect_stdout(io.StringIO()), controller.lock("backup"):
                pass
        self.assertIn("control-binding-changed: The control binding of instance a changed while backup was starting",
                      str(caught.exception))
        self.assertEqual(sorted(os.listdir(self.contexts["a"].operations_dir)), before)
        pf_instance.acquire_instance_lock(self.contexts["a"]).release()

    def test_cc6_a_second_init_never_touches_the_first_ones_build(self):
        source = self.candidate(block_on="import")
        rid = self.release_id(source)
        process, finish = self.popen_cli(["init", "--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                                         ["INSTALL CONTROL " + rid],
                                         script=source / "deploy/synology/install-control.sh")
        try:
            self.assertTrue(wait_for(self.base / "candidate.reached", process), (self.base / "bg-err.txt").read_text())
            build = self.leftovers()
            self.assertEqual(len(build), 1)
            before = snapshot(self.parent / build[0])
            result = self.init(launcher=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(f"  - install-busy: {self.parent / build[0]}:", result.stderr)
            self.assertEqual(snapshot(self.parent / build[0]), before)
        finally:
            pfx.release_fifo(self.base)
            returncode = finish()
        self.assertEqual(returncode, 0, (self.base / "bg-err.txt").read_text())
        self.assertEqual(pf_instance.validate_installation_root(self.new_root).blocking(), [])

    def test_cc7_two_no_clobber_launcher_creations(self):
        path = self.bin / "pf"
        runs = []
        for number in range(2):
            plan = {"operation_id": pf_install.new_operation_id(), "root": str(self.root), "kind": "init",
                    "launcher": {"path": str(path), "before_kind": "absent", "before_sha256": None, "action": "create",
                                 "after_text": pf_install.render_launcher(self.root).decode()}}
            runs.append(pf_install._Run(self.root, plan, "", {}, base=self.root, runner=None, interaction=silent()))
        first = runs[0]._apply_bind_launcher("bind-launcher")
        data = path.read_bytes()
        second = runs[1]._apply_bind_launcher("bind-launcher")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(pf_install._identity(path), first)
        self.assertEqual([name for name in os.listdir(self.bin) if name.startswith(".pf.tmp-")], [])


# ============================================================================ LG: the v2.5 operation lock


@ROOT_REQUIRED
class LegacyLock(InstallBase):
    instances = ()

    def test_lg1_a_held_v25_lock_makes_migration_busy(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={}, lock_held=True)
        try:
            before = snapshot(self.root, home["home"])
            code, out, err = self.run_pf(migrate_arguments(home, layout=self.layout), ["MIGRATE legacy"])
        finally:
            os.close(home["lock_fd"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("install-busy", conflict_codes(err))
        self.assertEqual(snapshot(self.root, home["home"]), before)

    def test_lg2_an_open_migration_gates_its_pinned_instance(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        request = {"legacy-home": str(home["home"]), "workspace": str(home["workspace"]), "slug": "legacy",
                   "no_launcher": True, "docker_endpoint": self.layout.daemon_endpoint}
        # effects: stage env, stage admin-config, validate, publish env, publish admin-config, register, verify
        self.assertEqual(self.forked("_apply_effect", "before", 6, lambda: self.operation("migrate-legacy", request)), 137)
        code, out, err = self.run_pf(["--instance", "legacy", "backup"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-operation-pending: Install operation", err)

    def test_lg3_a_v25_journal_created_after_the_summary_changes_the_plan(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})

        def create_pending():
            (home["state"] / "pending.json").write_text('{"operation": "update"}\n')
            return "MIGRATE legacy"

        before = snapshot(self.root, home["config"])
        code, out, err = self.run_pf(migrate_arguments(home, layout=self.layout), [create_pending])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-plan-changed:", err)
        self.assertIn("legacy-pending-operation", err)
        self.assertEqual(snapshot(self.root, home["config"]), before)


# ============================================================================ IP: identity preservation (A2-T04 offline)


@ROOT_REQUIRED
class IdentityPreservation(InstallBase):
    instances = ()

    def test_ip1_registration_and_upgrade_keep_every_identity(self):
        a = self.add_instance("a", workspace_name="checkout-a")
        self.fake.write_state(dict(pfx.default_docker_state(), **pfx.owned_topology(a)))
        self.fake.clear_calls()
        record_a = json.loads(a.record_path.read_bytes())
        env_a = (self.paths["a"]["configuration"] / ".env").read_bytes()
        identities_before = identities(self.fake)
        paths = pfx.data_home(self.base / "b", project="pf-b", group=GROUP)
        code, out, err = self.run_pf(register_arguments(self.layout, "b", paths, "pf-b"), ["REGISTER b"])
        self.assertEqual(code, 0, out + err)
        self.paths["b"] = paths
        source = self.candidate()
        code, out, err = self.run_pf(["install", "control", "--source", str(source)],
                                     ["INSTALL CONTROL " + self.release_id(source)])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Note: app-operation-required:", out)
        self.assertIn("the next 'pf --instance <slug> update' applies the new topology", out)
        after = json.loads(a.record_path.read_bytes())
        self.assertEqual({key: value for key, value in after.items() if key not in ("control", "record_revision")},
                         {key: value for key, value in record_a.items() if key not in ("control", "record_revision")})
        self.assertEqual(after["record_revision"], record_a["record_revision"] + 1)
        self.assertEqual(after["paths"]["workspace"], str(self.base / "a" / "checkout-a"))
        self.assertIsNone(pf_instance.load_registry(self.root).default_instance_id)
        self.assertEqual(identities(self.fake), identities_before)
        self.assertEqual({tuple(argv) for argv in self.fake.argvs()}, {tuple(PROBE)})
        self.assertEqual((self.paths["a"]["configuration"] / ".env").read_bytes(), env_a)
        pf_instance.set_default_instance(self.root, a.instance_id)
        code, out, err = self.run_pf(["install", "control", "--release", self.old_release.name],
                                     ["SELECT CONTROL " + self.old_release.name])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(pf_instance.load_registry(self.root).default_instance_id, a.instance_id)
        self.assertEqual(identities(self.fake), identities_before)
        record_evidence("IDENTITIES", {
            "resource_identities_before": identities_before, "resource_identities_after": identities(self.fake),
            "record_a_before": record_a, "record_a_after": json.loads(a.record_path.read_bytes()),
            "env_a_sha256_before": pf_instance.sha256_bytes(env_a),
            "env_a_sha256_after": pf_instance.sha256_bytes((self.paths["a"]["configuration"] / ".env").read_bytes()),
            "config_a_sha256": pf_instance.sha256_bytes((self.paths["a"]["configuration"] / "pf-config.json").read_bytes()),
            "docker_argv": self.fake.argvs()})
        record_evidence("cli/IP-1", {"stdout": out, "stderr": err})


# ============================================================================ RS: real signals through the installed CLI


@ROOT_REQUIRED
class RealSignals(BackgroundBase):

    def test_rs1_signals_during_the_control_smoke_cancel(self):
        source = self.candidate(block_on="import")
        rid = self.release_id(source)
        records, conf = self.records(), (self.root / "bootstrap/bootstrap.conf").read_bytes()
        for signum in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=signum.name):
                reached = self.base / "candidate.reached"
                if reached.exists():
                    reached.unlink()
                process, finish = self.popen_cli(["install", "control", "--source", str(source)],
                                                 ["INSTALL CONTROL " + rid])
                try:
                    self.assertTrue(wait_for(reached, process), (self.base / "bg-err.txt").read_text())
                    time.sleep(0.2)
                    process.send_signal(signum)
                finally:
                    returncode = finish()
                err = (self.base / "bg-err.txt").read_text()
                self.assertEqual(returncode, 1, err)
                self.assertIn("ERROR: install-cancelled: Cancelled during prepared; the private staging", err)
                journal = self.journal()
                self.assertEqual(journal["phase"], "cancelled")
                self.assertFalse((self.root / "install-operations" / journal["operation_id"] / "release.staging").exists())
                self.assertEqual(self.records(), records)
                self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)
                crash_row(kind="control", point="smoke", injection="real-signal:" + signum.name, resume_mode="none",
                          observed_generation=self.bound().name, old_release_retained=True, config_bytes_equal=True,
                          docker_argv=self.fake.argvs(), outcome=journal["phase"])
                record_evidence("cli/RS-1-" + signum.name, {"exit": returncode, "stderr": err})

    def test_rs2_signals_during_the_init_smoke_leave_nothing(self):
        source = self.candidate(block_on="import")
        rid = self.release_id(source)
        before = snapshot(self.parent)
        for signum in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=signum.name):
                reached = self.base / "candidate.reached"
                if reached.exists():
                    reached.unlink()
                process, finish = self.popen_cli(
                    ["init", "--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                    ["INSTALL CONTROL " + rid], script=source / "deploy/synology/install-control.sh")
                try:
                    self.assertTrue(wait_for(reached, process), (self.base / "bg-err.txt").read_text())
                    time.sleep(0.2)
                    process.send_signal(signum)
                finally:
                    returncode = finish()
                err = (self.base / "bg-err.txt").read_text()
                self.assertEqual(returncode, 1, err)
                self.assertIn("ERROR: install-cancelled: Cancelled during prepared; the private build directory", err)
                self.assertFalse(self.new_root.exists())
                self.assertEqual(self.leftovers(), [])
                self.assertEqual(snapshot(self.parent), before)
                crash_row(kind="init", point="smoke", injection="real-signal:" + signum.name, resume_mode="none",
                          observed_generation=None, old_release_retained=None, config_bytes_equal=None,
                          docker_argv=[], outcome="cancelled")

    def test_rs3_sigkill_during_verify_then_resume_leaves_exactly_the_new_generation(self):
        source = self.candidate(block_on="instances")
        rid = self.release_id(source)
        configs = self.config_bytes()
        process, finish = self.popen_cli(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        try:
            self.assertTrue(wait_for(self.base / "candidate.reached", process), (self.base / "bg-err.txt").read_text())
            process.send_signal(signal.SIGKILL)
        finally:
            finish()
        journal = self.journal()
        self.assertEqual(journal["phase"], "verifying")
        pfx.release_fifo(self.base)
        result = self.resume_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.journal(journal["operation_id"])["phase"], "completed")
        self.assert_one_generation(self.root / "releases" / rid)
        self.assert_release_verifies(self.old_release)
        self.assertEqual(self.config_bytes(), configs)
        crash_row(kind="control", point="verify", injection="real-signal:SIGKILL", resume_mode="resume",
                  observed_generation=rid, old_release_retained=True, config_bytes_equal=True,
                  docker_argv=self.fake.argvs(), outcome="completed")

    def test_rs4_sigkill_during_the_init_smoke_then_a_rerun_removes_the_own_leftover(self):
        source = self.candidate(block_on="import")
        rid = self.release_id(source)
        script = source / "deploy/synology/install-control.sh"
        process, finish = self.popen_cli(["init", "--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                                         ["INSTALL CONTROL " + rid], script=script)
        try:
            self.assertTrue(wait_for(self.base / "candidate.reached", process), (self.base / "bg-err.txt").read_text())
            process.send_signal(signal.SIGKILL)
        finally:
            finish()
        self.assertEqual(len(self.leftovers()), 1)
        pfx.release_fifo(self.base)
        time.sleep(0.5)
        result = pfx.run_installer(["init", "--root", str(self.new_root), "--interpreter", PY, "--no-launcher"],
                                   answers=["INSTALL CONTROL " + rid], script=script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Note: init-leftover-removed:", result.stdout)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(pf_instance.validate_installation_root(self.new_root).blocking(), [])
        crash_row(kind="init", point="smoke", injection="real-signal:SIGKILL", resume_mode="rerun",
                  observed_generation=rid, old_release_retained=None, config_bytes_equal=None, docker_argv=[],
                  outcome="completed")


# ============================================================================ CM: forked crash matrix (A2-T03)


def failing_instances(data):
    """A candidate pf-admin.py whose end-to-end ``instances`` fails (the installer's verify), nothing else."""
    hook = b'if __name__ == "__main__" and "instances" in __import__("sys").argv:\n    raise SystemExit(3)\n'
    return data.replace(b"from __future__ import annotations\n", b"from __future__ import annotations\n" + hook, 1)


@ROOT_REQUIRED
class CrashMatrix(InstallBase):
    """Every boundary of the journal (before/after each write) and of each effect (before/after each application).

    Boundaries with no disk change between them are the same state (before a call = after the previous call; the
    code between two seams only reads). Each distinct state is crashed once per resume mode, and every labelled
    boundary is recorded in INSTALL_CRASH_MATRIX with the state it is equal to.
    """

    def reset(self):
        self.tearDown()
        self.setUp()

    def sequence(self, body):
        """The seam call order of one uninterrupted run: [(seam, index, phase written or effect id)]."""
        calls, counters = [], {"_journal_write": 0, "_apply_effect": 0}
        stack = contextlib.ExitStack()
        for seam in counters:
            real = getattr(pf_install, seam)

            def wrapper(*args, _real=real, _seam=seam, **kwargs):
                detail = args[2] if _seam == "_apply_effect" else (json.loads(args[1]).get("phase") or "plan")
                calls.append((_seam, counters[_seam], detail))
                counters[_seam] += 1
                return _real(*args, **kwargs)

            stack.enter_context(mock.patch.object(pf_install, seam, wrapper))
        with stack:
            try:
                body()
            except pf_install.InstallError:
                pass
        return calls

    @staticmethod
    def states(calls):
        """[(canonical state, [labelled boundaries])]; a state is ("before", first call) or ("after", call)."""
        result = [(("before",) + calls[0][:2], [calls[0][:2] + ("before",)])]
        for position, (seam, index, detail) in enumerate(calls):
            labels = [(seam, index, "after")]
            if position + 1 < len(calls):
                labels.append(calls[position + 1][:2] + ("before",))
            result.append((("after", seam, index), labels))
        return result

    def crash(self, state, body):
        when, seam, index = state
        status = self.forked(seam, when, index, body)
        self.assertEqual(status, 137, f"the child did not reach {state}")

    def phase(self):
        names = [name for name in op_dirs(self.root) if not name.startswith(".")]
        return self.journal()["phase"] if names else None

    def record_rows(self, kind, labels, mode, **row):
        for seam, index, when in labels:
            crash_row(kind=kind, point=f"{when} {seam}[{index}]", injection="forked", resume_mode=mode, **row)

    # -- CM-1 / CM-2 / CM-3: control --source ----------------------------------------------

    def control_body(self, source):
        request = self.control_request(source)
        return lambda: self.operation("control", request)

    def test_cm1_control_source_every_boundary_with_resume_and_abandon(self):
        calls = self.sequence(self.control_body(self.candidate()))
        self.assertEqual([detail for seam, _, detail in calls if seam == "_apply_effect"],
                         ["stage-release", "smoke", "publish-release", "pre-bind-verify", "bind-bootstrap-conf",
                          "rebind-record", "rebind-record", "verify"])
        states = self.states(calls)
        for state, labels in states:
            for mode in ("resume", "abandon"):
                with self.subTest(state=state, mode=mode):
                    self.reset()
                    source = self.candidate()
                    rid = self.release_id(source)
                    configs = self.config_bytes()
                    self.crash(state, self.control_body(source))
                    item = self.open_operation()
                    committed = item is not None and any(effect["effect_id"] == "bind-bootstrap-conf"
                                                         for effect in item["journal"]["effects"])
                    result = self.resume_cli(abandon=mode == "abandon")
                    self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                    if result.returncode == 1:
                        self.assertIn("ERROR: install-cancelled:", result.stderr)
                    phase = self.phase()
                    self.assertIn(phase, (None, "completed", "cancelled", "abandoned"), result.stdout + result.stderr)
                    generation = self.assert_one_generation()
                    if mode == "abandon" and phase != "completed":
                        self.assertEqual(generation, self.old_release)
                    if mode == "resume" and committed:
                        self.assertEqual((phase, generation.name), ("completed", rid))
                    if phase == "completed":
                        self.assertEqual(generation.name, rid)
                    self.assert_release_verifies(self.old_release)
                    self.assertEqual(self.config_bytes(), configs)
                    self.assertEqual(self.fake.calls(), [])
                    self.assertIsNone(pf_instance.load_registry(self.root).default_instance_id)
                    if state == ("after", "_apply_effect", 4):  # after bind-bootstrap-conf ran, before its journal
                        record_evidence("cli/CM-1-sample-" + mode, {"state": state, "exit": result.returncode,
                                                                    "stdout": result.stdout, "stderr": result.stderr,
                                                                    "final_phase": phase})
                    self.record_rows("control", labels, mode, observed_generation=generation.name,
                                     old_release_retained=True, config_bytes_equal=True, docker_argv=[],
                                     outcome=phase or "no-operation")

    def apply_state(self, calls, effect_detail, occurrence=0):
        matches = [call for call in calls if call[0] == "_apply_effect" and call[2] == effect_detail]
        return ("after", "_apply_effect", matches[occurrence][1])

    def test_cm2_failure_after_apparent_success_is_observed_complete(self):
        calls = self.sequence(self.control_body(self.candidate()))
        for detail, effect_id in (("bind-bootstrap-conf", "bind-bootstrap-conf"), ("rebind-record", None)):
            with self.subTest(effect=detail):
                self.reset()
                source = self.candidate()
                first = sorted(self.contexts.values(), key=lambda context: context.instance_id)[0]
                effect_id = effect_id or "rebind-record:" + first.instance_id
                self.crash(self.apply_state(calls, detail), self.control_body(source))
                self.assertEqual(self.open_operation()["journal"]["effects"][-1]["state"], "intended")
                (self.root / "bootstrap" / "bootstrap.conf.tmp-deadbeef").write_bytes(b"partial")
                result = self.resume_cli()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Note: leftover temporary file " + str(self.root / "bootstrap" / "bootstrap.conf.tmp-deadbeef"),
                              result.stdout)
                journal = self.journal()
                entry = next(item for item in journal["effects"] if item["effect_id"] == effect_id)
                self.assertEqual((entry["state"], entry["observed"]), ("complete", "complete"))
                self.assertEqual(journal["phase"], "completed")
                self.assert_one_generation(self.root / "releases" / self.release_id(source))

    def test_cm3_a_mixed_binding_is_visible_read_only_and_gates_mutation(self):
        calls = self.sequence(self.control_body(self.candidate()))
        self.reset()
        source = self.candidate()
        first = sorted(self.contexts.values(), key=lambda context: context.instance_id)[0]
        self.crash(self.apply_state(calls, "rebind-record", 0), self.control_body(source))
        self.assertEqual(self.bound().name, self.release_id(source))
        before = snapshot(self.root)
        code, out, err = self.run_pf(["--instance", first.slug, "status"])
        self.assertIn("INSTALL OPERATION", out)
        self.assertIn("phase=switching", out)
        code, out, err = self.run_pf(["--instance", first.slug, "backup"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-operation-pending:", err)
        self.assertEqual(snapshot(self.root), before)

    # -- CM-4: verify failure and an interrupted restore ---------------------------------------

    def failing_candidate(self):
        return self.candidate(mutate={"deploy/synology/pf-admin.py": failing_instances})

    def test_cm4_verify_failure_restores_and_an_interrupted_restore_resumes(self):
        source = self.failing_candidate()
        rid = self.release_id(source)
        records, conf = self.records(), (self.root / "bootstrap/bootstrap.conf").read_bytes()
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: install-verify-failed: Control release {rid} did not verify after binding (", err)
        self.assertIn(f"the previous binding {self.old_release.name} was restored (operation", err)
        self.assertIn("rolled_back). The application was not touched.", err)
        self.assertEqual(self.journal()["phase"], "rolled_back")
        self.assertEqual(self.records(), records)
        self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)
        self.assert_release_verifies(self.root / "releases" / rid)
        self.reset()
        calls = self.sequence(self.control_body(self.failing_candidate()))
        rollback = [call for call in calls if call[0] == "_journal_write" and call[2] in ("rolling_back", "rolled_back")]
        self.assertTrue(rollback)
        points = [("after", "_journal_write", call[1]) for call in rollback[:-1]]
        points += [("restore-write", number) for number in range(3)]
        for point in points:
            for mode in ("resume", "abandon"):
                with self.subTest(point=point, mode=mode):
                    self.reset()
                    source = self.failing_candidate()
                    records, conf = self.records(), (self.root / "bootstrap/bootstrap.conf").read_bytes()
                    if point[0] == "restore-write":
                        self.crash_restore_write(point[1], self.control_body(source))
                    else:
                        self.crash(point, self.control_body(source))
                    self.assertEqual(self.open_operation()["journal"]["phase"], "rolling_back")
                    result = self.resume_cli(abandon=mode == "abandon")
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.journal()["phase"], "rolled_back")
                    self.assertEqual(self.records(), records)
                    self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)
                    self.assert_one_generation(self.old_release)
                    crash_row(kind="control-rollback", point=str(point), injection="forked", resume_mode=mode,
                              observed_generation=self.old_release.name, old_release_retained=True,
                              config_bytes_equal=True, docker_argv=[], outcome="rolled_back")

    def crash_restore_write(self, number, body):
        """Crash right after the ``number``-th restore write (a binding target written back to its before bytes)."""
        real = pf_instance._write_private_file
        state = {"rollback": 0}

        def write(path, data, mode=0o600):
            result = real(path, data, mode)
            if Path(path).name in ("bootstrap.conf", "record.json") and b"r-" in data:
                journal_dirs = list((self.root / "install-operations").glob("inst-*/journal.json"))
                phase = json.loads(journal_dirs[0].read_bytes())["phase"] if journal_dirs else None
                if phase == "rolling_back":
                    if state["rollback"] == number:
                        os._exit(137)
                    state["rollback"] += 1
            return result

        def patched():
            with mock.patch.object(pf_instance, "_write_private_file", write):
                body()

        self.assertEqual(self.forked("_journal_write", "after", 10 ** 6, patched), 137)

    # -- CM-5: an unknown observed state -----------------------------------------------------

    def test_cm5_an_unknown_state_needs_the_operator_then_resume_or_abandon(self):
        calls = self.sequence(self.control_body(self.candidate()))
        for variant in ("resume", "abandon"):
            with self.subTest(variant=variant):
                self.reset()
                source = self.candidate()
                rid = self.release_id(source)
                first = sorted(self.contexts.values(), key=lambda context: context.instance_id)[0]
                self.crash(self.apply_state(calls, "rebind-record", 1), self.control_body(source))
                good = first.record_path.read_bytes()
                edited = json.loads(good)
                edited["record_revision"] += 5
                first.record_path.write_bytes(pf_instance.normalize_json(edited))
                before = snapshot(self.root)
                result = self.resume_cli()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("ERROR: install-needs-operator: Operation", result.stderr)
                self.assertIn(f"found {first.record_path} in a state it did not write", result.stderr)
                journal = self.journal()
                self.assertEqual(journal["phase"], "needs_operator")
                self.assertIn(str(first.record_path), journal["last_error"])
                self.assertEqual(journal["next"], ["install resume", "install resume --abandon"])
                after = snapshot(self.root)
                changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
                self.assertEqual(changed, [str(self.root / "install-operations" / journal["operation_id"] / "journal.json")])
                for context in self.contexts.values():
                    code, out, err = self.run_pf(["--instance", context.slug, "backup"])
                    self.assertIn("ERROR: install-operation-pending:", err)
                code, out, err = self.run_pf(["install", "control", "--source", str(source)])
                self.assertIn("install-operation-pending", conflict_codes(err))
                first.record_path.write_bytes(good)
                result = self.resume_cli(abandon=variant == "abandon")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if variant == "resume":
                    self.assertEqual(self.journal()["phase"], "completed")
                    self.assert_one_generation(self.root / "releases" / rid)
                else:
                    self.assertEqual(self.journal()["phase"], "abandoned")
                    self.assert_one_generation(self.old_release)

    # -- CM-6: migrate-legacy ----------------------------------------------------------------

    def migrate_fixture(self):
        launcher_dir = self.base / "bin"
        launcher_dir.mkdir(exist_ok=True)
        os.chmod(launcher_dir, 0o755)
        legacy_launcher = launcher_dir / "pf-v25"
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={}, launcher=legacy_launcher)
        request = {"legacy-home": str(home["home"]), "workspace": str(home["workspace"]), "slug": "legacy",
                   "launcher_path": str(launcher_dir / "pf"), "no_launcher": False,
                   "docker_endpoint": self.layout.daemon_endpoint}
        return home, request, launcher_dir / "pf", legacy_launcher

    def test_cm6_migrate_every_boundary_with_resume_and_abandon(self):
        home, request, _, _ = self.migrate_fixture()
        calls = self.sequence(lambda: self.operation("migrate-legacy", request))
        self.assertEqual(self.journal()["phase"], "completed")
        for state, labels in self.states(calls):
            for mode in ("resume", "abandon"):
                with self.subTest(state=state, mode=mode):
                    self.reset()
                    home, request, launcher, legacy_launcher = self.migrate_fixture()
                    legacy = snapshot(home["workspace"], home["control"], legacy_launcher)
                    env_bytes = (home["workspace"] / ".env").read_bytes()
                    registry = (self.root / "registry/instances.json").read_bytes()
                    self.crash(state, lambda: self.operation("migrate-legacy", request))
                    for target in (home["config"] / ".env", home["config"] / "pf-config.json"):
                        self.assertTrue(not target.exists() or target.read_bytes() in (env_bytes, (
                            home["workspace"] / "deploy/synology/pf-config.json").read_bytes()))
                    result = self.resume_cli(abandon=mode == "abandon")
                    self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                    if result.returncode == 1:
                        self.assertIn("ERROR: install-cancelled:", result.stderr)
                    phase = self.phase()
                    self.assertIn(phase, (None, "completed", "cancelled", "abandoned"), result.stdout + result.stderr)
                    self.assertEqual(snapshot(home["workspace"], home["control"], legacy_launcher), legacy)
                    staged = [name for name in os.listdir(home["config"]) if ".pf-migrate-" in name]
                    self.assertEqual(staged, [])
                    self.assertEqual(pf_instance.pending_registrations(self.root), [])
                    if phase == "completed":
                        self.assertEqual((home["config"] / ".env").read_bytes(), env_bytes)
                        code, out, err = self.run_pf(["--instance", "legacy", "backup"])
                        self.assertIn("ERROR: legacy-control-active:", err)
                        self.assertEqual(launcher.read_bytes(), pf_install.render_launcher(self.root))
                    else:
                        self.assertEqual(sorted(os.listdir(home["config"])), [])
                        self.assertEqual((self.root / "registry/instances.json").read_bytes(), registry)
                        self.assertFalse(launcher.exists())
                        operation = [name for name in op_dirs(self.root) if not name.startswith(".")]
                        if phase == "abandoned" and any(effect["effect_id"] == "discard-registration"
                                                        for effect in self.journal()["effects"]):
                            self.assertTrue((self.root / "install-operations" / operation[0] / "discarded-instance"
                                             / "record.json").exists())
                    self.assertEqual(self.fake.argvs() and set(map(tuple, self.fake.argvs())) or {tuple(PROBE)},
                                     {tuple(PROBE)})
                    self.record_rows("migrate-legacy", labels, mode, observed_generation=self.bound().name,
                                     old_release_retained=True, config_bytes_equal=True, docker_argv=self.fake.argvs(),
                                     outcome=phase or "no-operation")

    # -- CM-7: init --------------------------------------------------------------------------

    def init_paths(self):
        parent = self.base / "parent"
        parent.mkdir(exist_ok=True)
        os.chmod(parent, 0o755)
        launcher_dir = self.base / "bin"
        launcher_dir.mkdir(exist_ok=True)
        os.chmod(launcher_dir, 0o755)
        return parent, parent / "install", launcher_dir / "pf"

    def init_body(self, root, launcher):
        arguments = ["--root", str(root), "--interpreter", PY, "--launcher-path", str(launcher)]
        rid = pfx.content_release_id()
        return lambda: pf_install.run_init(arguments, interaction(["INSTALL CONTROL " + rid]), source_root=REPO_PACKAGE)

    def test_cm7_init_every_boundary_then_resume_or_rerun(self):
        parent, root, launcher = self.init_paths()
        calls = self.sequence(self.init_body(root, launcher))
        self.assertEqual(pf_install.load_operation(root, op_dirs(root)[0])[2]["phase"], "completed")
        for state, labels in self.states(calls):
            with self.subTest(state=state):
                self.reset()
                parent, root, launcher = self.init_paths()
                self.crash(state, self.init_body(root, launcher))
                if root.exists():
                    operation = [name for name in op_dirs(root) if not name.startswith(".")][0]
                    phase = pf_install.load_operation(root, operation)[2]["phase"]
                    result = self.cli(["install", "resume"], [] if phase == "completed" else ["RESUME " + operation[-8:]],
                                      executable=root / "bootstrap" / "pf")
                    mode = "resume"
                else:
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                        code = self.init_body(root, launcher)()
                    result = types_result(code, stdout.getvalue())
                    mode = "rerun"
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(sorted(os.listdir(parent)), ["install"])
                self.assertEqual(pf_instance.validate_installation_root(root).blocking(), [])
                journals = [pf_install.load_operation(root, name)[2]["phase"] for name in op_dirs(root)]
                self.assertEqual(journals[-1], "completed")
                self.assertEqual(launcher.read_bytes(), pf_install.render_launcher(root))
                self.record_rows("init", labels, mode, observed_generation=Path(pf_bootstrap.parse_bootstrap_conf(
                    (root / "bootstrap/bootstrap.conf").read_bytes(), label="c")["control_release"]).name,
                    old_release_retained=None, config_bytes_equal=None, docker_argv=[], outcome="completed")

    # -- CM-8: register inside the A1 registration transaction ---------------------------------

    def test_cm8_register_crashes_inside_the_registration_converge_or_are_discarded(self):
        for seam in ("_write_registry", "_clear_reservation"):
            for mode in ("resume", "abandon"):
                with self.subTest(seam=seam, mode=mode):
                    self.reset()
                    paths = pfx.data_home(self.base / "c", project="pf-c", group=GROUP)
                    request = {"slug": "c", "project": "pf-c", "workspace": str(paths["workspace"]),
                               "configuration": str(paths["configuration"]), "backups": str(paths["backups"]),
                               "recovery": str(paths["recovery"]), "docker_endpoint": self.layout.daemon_endpoint}
                    registry = (self.root / "registry/instances.json").read_bytes()

                    def body():
                        def crash(*args, **kwargs):
                            os._exit(137)
                        with mock.patch.object(pf_instance, seam, crash):
                            self.operation("register", request)

                    self.assertEqual(self.forked("_journal_write", "after", 10 ** 6, body), 137)
                    plan = self.open_operation()["plan"]
                    pinned = plan["instance"]["instance_id"]
                    self.assertTrue((self.root / "registry/reservations" / (pinned + ".json")).exists())
                    result = self.resume_cli(abandon=mode == "abandon")
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    current = pf_instance.load_registry(self.root)
                    self.assertEqual(pf_instance.pending_registrations(self.root), [])
                    self.assertIsNone(current.default_instance_id)
                    if mode == "resume":
                        self.assertEqual(self.journal()["phase"], "completed")
                        self.assertEqual(current.entry(pinned).slug, "c")
                    else:
                        self.assertEqual(self.journal()["phase"], "abandoned")
                        self.assertEqual((self.root / "registry/instances.json").read_bytes(), registry)
                        operation = self.journal()["operation_id"]
                        self.assertTrue((self.root / "install-operations" / operation / "discarded-instance").is_dir())
                    crash_row(kind="register", point=f"before pf_instance.{seam}", injection="forked",
                              resume_mode=mode, observed_generation=self.bound().name, old_release_retained=True,
                              config_bytes_equal=True, docker_argv=self.fake.argvs(),
                              outcome=self.journal()["phase"])


# ============================================================================ AU: audit regressions


class _Hung(Exception):
    """Raised by the SIGALRM guard when a read blocks (a FIFO opened without O_NONBLOCK)."""


@contextlib.contextmanager
def alarm_guard(seconds=20):
    def expired(signum, frame):
        raise _Hung(f"blocked for {seconds} s")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def patched_rename(target, failure):
    """os.rename that raises ``failure`` (an exception instance or class) when renaming onto ``target``."""
    real = os.rename

    def rename(source, destination, *args, **kwargs):
        if str(destination) == str(target):
            raise failure
        return real(source, destination, *args, **kwargs)

    return mock.patch.object(pf_install.os, "rename", rename)


def crash_after_link(target):
    """Inside a forked child: end the process right after ``os.link(temp, target)`` (before the temp unlink)."""
    real = os.link

    def link(source, destination, *args, **kwargs):
        real(source, destination, *args, **kwargs)
        if str(destination) == str(target):
            os._exit(137)

    return mock.patch.object(pf_install.os, "link", link)


@ROOT_REQUIRED
class AuditRegressions(InitBase):
    """Each test fails on 964aefb and passes with the audit fix (audit-findings.json names the finding)."""

    instances = ("a",)

    def init_arguments(self, root=None, launcher=None):
        arguments = ["--root", str(root or self.new_root), "--interpreter", PY]
        return arguments + (["--launcher-path", str(launcher)] if launcher is not None else ["--no-launcher"])

    def init_resume(self, root):
        operation = [name for name in op_dirs(root) if not name.startswith(".")][0]
        return self.cli(["install", "resume"], ["RESUME " + operation[-8:]], executable=root / "bootstrap" / "pf")

    def temps(self, directory):
        return [name for name in os.listdir(directory) if name.startswith(".pf.tmp-")]

    def migrate_request(self, home, launcher=None):
        return {"legacy-home": str(home["home"]), "workspace": str(home["workspace"]), "slug": "legacy",
                "no_launcher": launcher is None, "launcher_path": None if launcher is None else str(launcher),
                "docker_endpoint": self.layout.daemon_endpoint}

    # AF-01: the under-lock re-check sees another init's busy build.
    def test_au1_a_second_init_that_confirmed_concurrently_refuses_under_the_locks(self):
        other = self.parent / ".install.init-0badc0de"
        held = []

        def confirm_while_another_init_builds():
            pf_instance._create_private_dir(other, 0o700)
            fd = os.open(str(other), os.O_RDONLY | os.O_DIRECTORY)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(fd)
            return "INSTALL CONTROL " + self.rid

        try:
            code, out, err = self.run_init(self.init_arguments(), [confirm_while_another_init_builds])
        finally:
            for fd in held:
                os.close(fd)
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-plan-changed:", err)
        self.assertIn("install-busy", err)
        self.assertFalse(self.new_root.exists())
        self.assertEqual(self.leftovers(), [other.name])

    # AF-01/AF-08: a stop or failure at the root rename is before the commit point.
    def test_au2_a_failed_or_interrupted_root_rename_cancels_and_never_names_resume(self):
        cases = (("EXDEV", OSError(errno.EXDEV, "Invalid cross-device link"), "install-failed"),
                 ("EBUSY", OSError(errno.EBUSY, "Device or resource busy"), "install-failed"),
                 ("interrupt", KeyboardInterrupt, "install-cancelled"))
        for label, failure, code_expected in cases:
            with self.subTest(case=label):
                self.new_root.mkdir()
                os.chmod(self.new_root, 0o755)
                with patched_rename(self.new_root, failure):
                    code, out, err = self.run_init(self.init_arguments(), ["INSTALL CONTROL " + self.rid])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: {code_expected}:", err)
                self.assertIn("private build directory", err)
                self.assertNotIn("install resume", err)
                self.assertEqual(os.listdir(self.new_root), [])
                self.assertEqual(self.leftovers(), [])
                os.rmdir(self.new_root)
        code, out, err = self.run_init(self.init_arguments(), ["INSTALL CONTROL " + self.rid])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(pf_instance.validate_installation_root(self.new_root).blocking(), [])

    # AF-08: an empty root that is a mount point (or another device) is refused before anything is written.
    def test_au3_an_empty_root_on_another_filesystem_is_refused_by_the_preflight(self):
        self.new_root.mkdir()
        os.chmod(self.new_root, 0o755)
        request = {"root": str(self.new_root), "source_root": str(REPO_PACKAGE), "interpreter": PY, "tools": {},
                   "no_launcher": True}
        result = pf_install._preflight(None, "init", request, runner=None, running_release=None)
        self.assertEqual(result.conflicts, [])
        real = os.path.ismount
        with mock.patch.object(pf_install.os.path, "ismount", lambda path: str(path) == str(self.new_root) or real(path)):
            result = pf_install._preflight(None, "init", request, runner=None, running_release=None)
        conflicts = [item for item in result.conflicts if item.code == "root-exists"]
        self.assertEqual(len(conflicts), 1, result.conflicts)
        self.assertIn("mount point or another filesystem than its parent", conflicts[0].detail)

    # AF-02: an own build stopped between creating install-operations/ and the intent is recognized.
    def test_au4_an_empty_install_operations_in_an_own_build_is_an_own_leftover(self):
        foreign = self.parent / ".install.init-0123abcd"
        pf_instance._create_private_dir(foreign, 0o700)
        pf_instance._create_private_dir(foreign / "install-operations", 0o700)
        (foreign / "data").write_text("not ours")
        code, out, err = self.run_init(self.init_arguments(), ["INSTALL CONTROL " + self.rid])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"  - init-leftover-unknown: {foreign}:", err)
        os.unlink(foreign / "data")
        code, out, err = self.run_init(self.init_arguments(), ["INSTALL CONTROL " + self.rid])
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Note: init-leftover-removed: {foreign} is an unpublished build", out)
        self.assertEqual(sorted(os.listdir(self.parent)), ["install"])

    # AF-03: --source and --release are never re-guessed from the value.
    def test_au5_the_typed_control_flag_decides_source_or_release(self):
        source = self.candidate()
        before = snapshot(self.root)
        code, out, err = self.run_pf(["install", "control", "--release", str(source)])
        self.assertEqual(code, 1, out + err)
        self.assertEqual(conflict_codes(err), ["release-not-retained"], err)
        self.assertNotIn("INSTALL CONTROL", out)
        for value in ("candidate", self.old_release.name):
            with self.subTest(source=value):
                code, out, err = self.run_pf(["install", "control", "--source", value])
                self.assertEqual(code, 1, out + err)
                self.assertEqual(conflict_codes(err), ["source-missing"], err)
                self.assertIn("the source must be one canonical absolute path", err)
        self.assertEqual(snapshot(self.root), before)

    # AF-04: a wrapper-only candidate says which wrapper it does not install.
    def test_au6_a_wrapper_only_candidate_names_the_wrapper_it_does_not_install(self):
        source = pfx.candidate_copy(self.base, name="wrapper-only",
                                    mutate={"deploy/synology/backup.sh": lambda data: data + b"# wrapper fix\n"})
        before = snapshot(self.root)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)])
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Release {self.old_release.name} is already the bound control; nothing was changed. The "
                      "candidate's scheduler wrapper(s) backup.sh differ from", out)
        self.assertIn("installed together with the next control release whose files change.", out)
        self.assertNotIn("release-check.sh", out)
        self.assertEqual(snapshot(self.root), before)

    # AF-05: a stop after the intent rename cancels the open operation instead of claiming nothing was written.
    def test_au7_an_interrupt_after_the_intent_rename_cancels_the_open_operation(self):
        source = self.candidate()
        operations_dir = self.root / "install-operations"
        real = pf_instance._fsync_directory
        fired = []

        def fsync(path):
            if Path(path) == operations_dir and not fired:
                fired.append(path)
                raise KeyboardInterrupt
            return real(path)

        records = self.records()
        with mock.patch.object(pf_instance, "_fsync_directory", fsync):
            code, out, err = self.run_pf(["install", "control", "--source", str(source)],
                                         ["INSTALL CONTROL " + self.release_id(source)])
        self.assertEqual(code, 1, out + err)
        self.assertTrue(fired)
        journal = self.journal()
        self.assertEqual(journal["phase"], "cancelled")
        self.assertIn(f"ERROR: install-cancelled: Cancelled during planned; the private staging of operation "
                      f"{journal['operation_id']} was removed and the installed control is unchanged.", err)
        self.assertIsNone(self.open_operation())
        self.assertEqual(self.records(), records)

    # AF-09: the .env hash stays in plan.json only.
    def test_au8_the_env_hash_is_never_printed_or_journaled(self):
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        request = self.migrate_request(home)
        # effects: stage env, stage admin-config, validate, publish env (intended, then the crash)
        self.assertEqual(self.forked("_apply_effect", "before", 3, lambda: self.operation("migrate-legacy", request)),
                         137)
        data = (home["workspace"] / ".env").read_bytes()
        digest = pf_instance.sha256_bytes(data)
        target = home["config"] / ".env"
        self.assertFalse(target.exists())
        target.write_bytes(data)  # the same bytes, another identity: written by someone else after the crash
        result = self.resume_cli()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR: install-needs-operator:", result.stderr)
        self.assertIn("expected absent or the planned .env copy, found a file with the planned bytes but another "
                      "identity", result.stderr)
        journal = self.journal()
        directory = self.root / "install-operations" / journal["operation_id"]
        for label, text in (("stdout", result.stdout), ("stderr", result.stderr),
                            ("journal", (directory / "journal.json").read_text())):
            with self.subTest(where=label):
                self.assertNotIn(digest, text)
                self.assertNotIn(digest[:12], text)
        self.assertIn(digest, (directory / "plan.json").read_text())

    # AF-10: a launcher another writer created before resume is left, never needs_operator.
    def test_au9_a_launcher_created_by_another_writer_before_resume_is_left(self):
        foreign = b"#!/bin/sh\necho another writer\n"
        # init effects: build-root 0, smoke 1, publish-root 2, verify 3, bind-launcher 4
        for label, when, index in (("bind-launcher intended", "before", 4), ("before bind-launcher", "after", 2)):
            with self.subTest(case=label):
                root = self.parent / ("install-" + str(index))
                launcher = self.bin / ("pf-" + str(index))
                body = lambda: pf_install.run_init(self.init_arguments(root, launcher),  # noqa: E731
                                                   interaction(["INSTALL CONTROL " + self.rid]),
                                                   source_root=REPO_PACKAGE)
                self.assertEqual(self.forked("_apply_effect", when, index, body), 137)
                self.assertFalse(launcher.exists())
                launcher.write_bytes(foreign)
                result = self.init_resume(root)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"Note: launcher-left: {launcher} appeared while installing", result.stdout)
                self.assertEqual(launcher.read_bytes(), foreign)
                operation = [name for name in op_dirs(root) if not name.startswith(".")][0]
                journal = pf_install.load_operation(root, operation)[2]
                self.assertEqual(journal["phase"], "completed")
                entry = next(item for item in journal["effects"] if item["effect_id"] == "bind-launcher")
                self.assertEqual((entry["state"], entry["target_identity"]), ("complete", None))
                self.assertIsNone(journal["result"]["launcher"])

    # AF-11: the hard-linked launcher temp of a stop inside bind-launcher is removed by resume and by abandon.
    def test_au10_a_stop_between_the_launcher_link_and_the_temp_unlink_leaves_no_temp(self):
        launcher = self.bin / "pf-init"

        def init_body():
            with crash_after_link(launcher):
                pf_install.run_init(self.init_arguments(self.new_root, launcher),
                                    interaction(["INSTALL CONTROL " + self.rid]), source_root=REPO_PACKAGE)

        self.assertEqual(self.forked("_journal_write", "after", 10 ** 6, init_body), 137)
        self.assertEqual(len(self.temps(self.bin)), 1)
        result = self.init_resume(self.new_root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.temps(self.bin), [])
        self.assertEqual(launcher.read_bytes(), pf_install.render_launcher(self.new_root))
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        migrate_launcher = self.bin / "pf-migrate"
        request = self.migrate_request(home, migrate_launcher)

        def migrate_body():
            with crash_after_link(migrate_launcher):
                self.operation("migrate-legacy", request)

        self.assertEqual(self.forked("_journal_write", "after", 10 ** 6, migrate_body), 137)
        self.assertEqual(len(self.temps(self.bin)), 1)
        result = self.resume_cli(abandon=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.journal()["phase"], "abandoned")
        self.assertFalse(migrate_launcher.exists())
        self.assertEqual(self.temps(self.bin), [])

    # AF-12: an unreadable entry of install-operations/ names a manual step; resume does not say "nothing to do".
    def test_au11_an_unreadable_entry_names_its_manual_step(self):
        entry = self.root / "install-operations" / "lost+found"
        entry.mkdir(mode=0o700)
        code, out, err = self.run_pf(["--instance", "a", "backup"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-operation-pending: Install operation lost+found (unreadable)", err)
        self.assertIn(f"inspect {entry} as root and move it out of", err)
        self.assertNotIn("(none)", err)
        code, out, err = self.run_pf(["install", "status"])
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"next: inspect {entry} as root", out)
        code, out, err = self.run_pf(["install", "resume"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: install-operation-unreadable: {entry} is not a readable install operation", err)
        self.assertNotIn("nothing to do", out + err)

    # AF-13: a FIFO in an editor-writable place never blocks the installer.
    def test_au12_a_fifo_never_blocks_the_installer(self):
        env = self.paths["a"]["configuration"] / ".env"
        env.unlink()
        os.mkfifo(str(env), 0o600)
        with alarm_guard():
            self.assertEqual(pf_install._config_categories(self.contexts["a"]), ("ok", "invalid"))
        env.unlink()
        env.write_text(pfx.ENV_TEXT)
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={})
        request = self.migrate_request(home)
        runner = pf_install._installed_runner(self.root, request["docker_endpoint"])
        result = pf_install._preflight(self.root, "migrate-legacy", request, runner=runner,
                                       running_release=self.bound())
        self.assertEqual(result.conflicts, [])
        source = home["workspace"] / ".env"
        source.unlink()
        os.mkfifo(str(source), 0o600)  # swapped in after the summary
        with alarm_guard(), self.assertRaises(pf_install.InstallError) as caught:
            pf_install.execute(self.root, result.plan, runner=runner, interaction=silent(), candidate=None,
                               running_release=self.bound(), request=None)
        self.assertEqual(caught.exception.code, "install-plan-changed")
        self.assertEqual(self.journal()["phase"], "cancelled")
        self.assertEqual(sorted(os.listdir(home["config"])), [])

    # AF-14: an invalid admin configuration does not hide the other migration conflicts.
    def test_au13_an_invalid_admin_config_still_reports_every_other_conflict(self):
        open_bin = self.base / "open-bin"
        open_bin.mkdir()
        os.chmod(open_bin, 0o777)
        home = pfx.legacy_home(self.base, env_repo=pfx.ENV_TEXT, admin_legacy={}, pending=True)
        (home["workspace"] / "deploy/synology/pf-config.json").write_text("{not json\n")
        before = snapshot(self.root, home["home"])
        code, out, err = self.run_pf(migrate_arguments(home, launcher=open_bin / "pf", layout=self.layout))
        self.assertEqual(code, 1, out + err)
        codes = conflict_codes(err)
        self.assertIn("admin-config-invalid", codes)
        self.assertIn("launcher-parent-untrusted", codes)
        self.assertNotIn("project-invalid", codes)
        self.assertIn("Note: legacy-state-unchecked:", out)
        self.assertEqual(snapshot(self.root, home["home"]), before)

    # AF-15: an interrupted automatic restore is reported open, never as "nothing was changed".
    def test_au14_an_interrupted_automatic_restore_is_reported_open(self):
        source = self.candidate(mutate={"deploy/synology/pf-admin.py": failing_instances})
        records, conf = self.records(), (self.root / "bootstrap/bootstrap.conf").read_bytes()
        real = pf_instance._write_private_file

        def write(path, data, mode=0o600):
            if Path(path).name in ("bootstrap.conf", "record.json"):
                journals = list((self.root / "install-operations").glob("inst-*/journal.json"))
                if journals and json.loads(journals[0].read_bytes())["phase"] == "rolling_back":
                    raise KeyboardInterrupt
            return real(path, data, mode)

        with mock.patch.object(pf_instance, "_write_private_file", write):
            code, out, err = self.run_pf(["install", "control", "--source", str(source)],
                                         ["INSTALL CONTROL " + self.release_id(source)])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-interrupted: Control release", err)
        self.assertIn("stays open in phase rolling_back", err)
        self.assertIn("install resume' to finish the restore", err)
        self.assertNotIn("nothing was changed", err)
        self.assertEqual(self.journal()["phase"], "rolling_back")
        result = self.resume_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.journal()["phase"], "rolled_back")
        self.assertEqual(self.records(), records)
        self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)

    # AF-15: a stop while a cancel removes a published release never leaves it partial under releases/.
    def test_au15_a_cancel_after_publication_never_leaves_a_partial_release(self):
        source = self.candidate()
        rid = self.release_id(source)
        real_apply, real_remove = pf_install._apply_effect, pf_instance._remove_tree

        def apply(run, effect_id, etype):
            if etype == "pre-bind-verify":
                raise pf_install._SmokeFailed("forced pre-bind failure")
            return real_apply(run, effect_id, etype)

        def remove(path):
            path = Path(path)
            if path.name in (rid, "release.cancelled"):
                os.unlink(str(sorted(path.iterdir())[0]))  # a partial removal, then the stop
                os._exit(137)
            return real_remove(path)

        def body():
            with mock.patch.object(pf_install, "_apply_effect", apply), \
                    mock.patch.object(pf_instance, "_remove_tree", remove):
                self.operation("control", self.control_request(source))

        self.assertEqual(self.forked("_journal_write", "after", 10 ** 6, body), 137)
        self.assertEqual(self.journal()["phase"], "cancelled")
        self.assertFalse((self.root / "releases" / rid).exists())
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 0, out + err)
        self.assert_one_generation(self.root / "releases" / rid)

    # AF-07/AF-18: no module-level import of the entry or installer module is unused.
    def test_au16_no_unused_module_imports(self):
        for name in ("pf-admin.py", "pf_install.py"):
            with self.subTest(module=name):
                tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
                imported = {(alias.asname or alias.name).split(".")[0]
                            for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
                used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                self.assertEqual(sorted(imported - used), [])


class types_result:  # noqa: N801 - a CompletedProcess-shaped value for in-process reruns
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


if __name__ == "__main__":
    unittest.main()
