"""PF-A1.4 acceptance and regression tests: every entry route on the A1 primitives, no catch-all Compose.

Case mapping (PF-A1.4 SPEC section 6.1; design r3 acceptance case A1-T15 and the A1 re-runs):
  DT-1..DT-9   dispatch and entry-route tables      -> DispatchTables (static/unit; E8 installed)
  RC-1..RC-5   removed routes and options           -> RemovedRoutes (in-process, fake daemon)
  RA-1..RA-8   bounded read-only views ps/logs       -> ReadOnlyViews
  US-1..US-8   unattended gate, policy, auto-apply   -> UnattendedGate
  SW-1..SW-6   scheduler wrappers                    -> SchedulerWrappers (shell; SW-6 installed)
  EH-1..EH-6   fail_closed on the exact inventory    -> ErrorHandler
  CI-1..CI-6   cross-instance and restore authority  -> CrossInstance, PurgeResumeAuthority
  SS-1..SS-7   static scan of the release            -> StaticScan
  CLI-1..CLI-7 installed launcher transcripts        -> InstalledLauncherRoutes (CLI-4: the existing
               A1-T16 test test_instance_context.PendingJournalVisibility.test_installed_cli_status_shows_
               journal_before_live_data_errors)

Docker is never contacted: the registered ``docker`` tool is ``tests/fake_docker.py`` (state in a JSON
file, every call recorded) or a recording stub. Nothing here touches a real daemon, a real NAS or the
running PartFlow stack. When ``PF_A14_EVIDENCE`` names a directory, the CLI/SW/EH cases also write their
transcripts and before/after fake-daemon identities there (checkpoint evidence only; never asserted).
"""
import argparse
import ast
import contextlib
import copy
import grp
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_docker = pf.pf_docker
pf_instance = pfx.pf_instance
PACKAGE = pfx.PACKAGE
REPO_PACKAGE = pfx.REPO_PACKAGE
GROUP = grp.getgrgid(os.getgid()).gr_name
ROOT_REQUIRED = unittest.skipUnless(os.geteuid() == 0, "protected fixtures require root ownership (uid 0)")
PROJECT = "partflow-staging"
PROBE = ["info", "--format", "{{json .}}"]
RELEASE_MODULES = ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "pf_runner.py", "pf_config.py", "pf_source.py",
                   "pf_docker.py", "pf_install.py")
SHELL_ENTRY_POINTS = (REPO_PACKAGE / "pf.sh", PACKAGE / "install-control.sh", PACKAGE / "backup.sh",
                      PACKAGE / "release-check.sh")
GATE_CODES = ("terminal-required", "instance-required-unattended", "policy-grant-required")
# Words the removed catch-all route used to forward (PF-A1.3 COMPOSE_MUTATING_VERBS / read-only verbs).
FORMER_MUTATING_VERBS = {"up", "down", "start", "stop", "restart", "pull", "build", "create", "rm", "exec", "run",
                         "kill", "pause", "unpause", "cp", "push", "scale", "wait", "attach", "watch"}
FORMER_READ_ONLY_VERBS = {"ps", "logs", "version", "top", "images", "port", "ls", "events", "stats"}
BACKUP_ID = "20261006T000000Z-" + pfx.OLD[:12] + "-abcdef"
RECOVERY_ID = "purge-20261006T000000Z-" + pfx.OLD[:12] + "-abcdef"


class Terminal:
    """``sys.stdin`` stand-in whose ``isatty()`` answers the case value (``unattended()`` stays real)."""

    def __init__(self, value):
        self.value = value

    def isatty(self):
        return self.value


def fixture_context():
    """PF-A3.2: the identity fields a lifecycle plan names, without an installation (pure table tests)."""
    return types.SimpleNamespace(
        instance_id="3f0c6a8e-1b2d-4c5e-9f00-a1b2c3d4e5f6", slug="staging", compose_project=PROJECT,
        daemon=types.SimpleNamespace(engine_id=pfx.ENGINE_ID), record_sha256="1" * 64,
        control=types.SimpleNamespace(release_id="pf-fixture", sha256="2" * 64),
        profile=types.SimpleNamespace(id="partflow-staging-legacy", version="2.5.0-a1.1", sha256="3" * 64),
        approved_policy=types.SimpleNamespace(revision=1, sha256="4" * 64),
        paths=types.SimpleNamespace(workspace=Path("/volume1/partflow/repo")))


def record_evidence(name, payload):
    directory = os.environ.get("PF_A14_EVIDENCE")
    if not directory:
        return
    path = Path(directory) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


def parse_module(name):
    source = (PACKAGE / name).read_text(encoding="utf-8")
    return source, ast.parse(source, filename=name, feature_version=(3, 9))


def qualified_calls(tree):
    """[(call node, enclosing qualified function name)] for every call in ``tree``."""
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = scope + (child.name,) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                  ast.ClassDef)) else scope
            if isinstance(child, ast.Call):
                found.append((child, ".".join(scope) or "<module>"))
            visit(child, inner)

    visit(tree, ())
    return found


def dotted(node):
    """``a.b.c`` for a Name/Attribute chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


# ------------------------------------------------------------------ SS-3 mutation and write sites

# PF-A2.3: fchmod/fchown joined the tracked vocabulary (the permission engine changes metadata through descriptors).
# PF-A2.3 audit: ftruncate/truncate too (the resume cuts a torn journal tail in place), in every module.
OS_WRITES = {"chmod", "chown", "mkdir", "replace", "rename", "fchmod", "fchown", "ftruncate", "truncate"}
WRITER_NAMES = {"write_json", "write_private_json", "_write_private", "_write_private_file"}
TEMP_WRITES = {"TemporaryDirectory", "mkdtemp", "mkstemp", "NamedTemporaryFile"}
MODE_RE = re.compile(r"[rwxabt+]+(?::[a-z0-9]*)?\Z")


def _literal_mode(call):
    """The literal mode of ``open(path, mode)``, ``Path.open(mode)`` or ``tarfile.open(name, mode)``."""
    candidates = [keyword.value for keyword in call.keywords if keyword.arg == "mode"] or list(call.args[:2])
    for value in candidates:
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and MODE_RE.match(value.value):
            return value.value
    return None


def tracked_write(call):
    """The SS-3 name of a mutation/write call, or None."""
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in WRITER_NAMES:
            return func.id
        if func.id == "open":
            mode = _literal_mode(call)
            return "open" if mode and set(mode) & set("wax+") else None
        return None
    if not isinstance(func, ast.Attribute):
        return None
    attr = func.attr
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if owner == "os":
        return "os." + attr if attr in OS_WRITES else None
    if owner == "shutil" and (attr.startswith("copy") or attr in ("rmtree", "move")):
        return "shutil." + attr
    if owner == "tempfile" and attr in TEMP_WRITES:
        return "tempfile." + attr
    if attr in ("mkdir", "unlink", "write_text", "write_bytes"):
        return "." + attr
    if attr in WRITER_NAMES:
        return attr
    if attr == "open":
        mode = _literal_mode(call)
        return ".open" if mode and set(mode) & set("wax+") else None
    return None


def write_sites(tree):
    return {(name, where) for call, where in qualified_calls(tree) for name in [tracked_write(call)] if name}


# Captured once at base 53246a1 with write_sites() (unchanged by PF-A1.4: no route of this slice adds a
# write site). A new site fails SS-3 until it is classified here with its reason.
WRITE_SITE_ALLOWLIST = {
    "pf-admin.py": {
        (".mkdir", "Controller.ensure_backup_tree"),
        (".mkdir", "Controller.ensure_recovery_tree"),
        (".mkdir", "Controller.lock"),
        (".mkdir", "Controller.restore_runtime_environment"),
        (".open", "Controller.create_tree_archive"),
        (".open", "write_json"),
        
        
        (".unlink", "Controller.restore_instance"), 
        (".write_bytes", "Controller.make_override"),
        ("_write_private", "Controller.write_deletion_plan"), ("_write_private", "Controller.write_private_json"),
        ("os.chmod", "Controller.begin_operation"),
        ("os.chmod", "Controller.freeze_app_config"), ("os.chmod", "Controller.lock"),
        ("os.mkdir", "Controller.begin_operation"), ("os.mkdir", "Controller.freeze_app_config"),
        ("os.replace", "Controller.make_override"),
        ("os.replace", "Controller.restore_runtime_environment"), ("os.replace", "write_json"),
        # PF-A2.2: the config wizard's compare-and-swap publish (replace mode) and its audit record.
        ("os.replace", "write_editable_file"), ("write_private_json", "Controller.write_config_change"),
        
        # PF-A2.3 (section 3.10): the content-only copy of every flow (no mode or xattr is copied): mkdir 0700 for a
        # directory, the bytes of a no-follow, identity-checked source descriptor into a private temporary sibling,
        # then the rename that makes the copy a new inode; all relative to held directory descriptors (audit fix).
        ("os.mkdir", "_copy_fresh_at"), ("shutil.copyfileobj", "_copy_fresh_at"), ("os.replace", "_copy_fresh_at"),
        # PF-A2.3: the config writer's create path (fchown/fchmod of its own private temporary, then fstat check).
        ("os.fchown", "write_editable_file"), ("os.fchmod", "write_editable_file"),
        # PF-A2.3 `permissions apply` (sections 3.6/3.7), all inside the instance lock and the operation directory:
        # the confirmed change list, the frozen plan, the approval's op-dir copy and the protected record
        # (_write_private_file), the outcome, the resume's own change list; pending.json (open, phase, confirmed
        # plans) and its removal at close or abandon; the fence and its restore on the held root descriptor; the
        # compensation of --abandon (fchown, then fchmod, of an object re-opened no-follow and identity-checked).
        ("_write_private_file", "Controller._write_permission_intent"),
        ("_write_private_file", "Controller._permission_execute"),
        ("_write_private_file", "Controller._write_permission_outcome"),
        ("_write_private_file", "Controller._permissions_resume"),
        ("write_json", "Controller.permissions_apply"), ("write_json", "Controller._permissions_resume"),
        ("write_json", "Controller._mark_interrupted"),
        (".unlink", "Controller._permission_execute"), (".unlink", "Controller._permissions_abandon"),
        ("os.fchmod", "Controller._permission_execute"), ("os.fchmod", "Controller._restore_fences"),
        ("os.fchown", "Controller._permissions_abandon"), ("os.fchmod", "Controller._permissions_abandon"),
        # PF-A2.3 audit: --resume cuts only the torn, never-executed final line of the original apply's write-ahead
        # journal, inside the instance lock and after the RESUME confirmation (implementation report deviation 7).
        ("os.ftruncate", "Controller._permissions_resume"),
        ("tempfile.TemporaryDirectory", "Controller.create_deployed_source_archive"),
        ("tempfile.TemporaryDirectory", "Controller.deploy"), ("tempfile.TemporaryDirectory", "Controller.prove_tree_commit"),
        ("tempfile.TemporaryDirectory", "Controller.restore_instance"), ("tempfile.TemporaryDirectory", "Controller.rollback"),
        ("tempfile.TemporaryDirectory", "Controller.update"), ("tempfile.mkdtemp", "Controller.restore_revision_checkpoints"),
        ("write_json", "Controller.begin_operation"),
        ("write_json", "Controller.resolve"),
        ("write_private_json", "Controller._append_envelope_record"), ("write_private_json", "Controller._record_daemon"),
        ("write_private_json", "Controller.binding_blocked"),
        ("write_private_json", "Controller.purge"), ("write_private_json", "Controller.require_empty_target"),
        ("write_private_json", "Controller.require_topology_owned"),
        # PF-A3.1 (sections 3.1-3.8), all inside a locked operation unless noted: the private artifact directories
        # (private_dir: mkdir 0700 + chmod), the deployment staging directory and its files, the seal's copies and
        # record, the pointer and journal close of finish_deployment, the capture folder, dump (exclusive create, then
        # the atomic rename of the .partial) and list, the sealed manifest and its hash, the external verification
        # record, the captures/deployment-artifact audit records, the purge bundle's folders, copies and sealed
        # manifest, the format 1 .env moved out of a restored source tree, and the legacy migration record and
        # migrated bytes written by the strict reader only inside an open operation (READ_ONLY_REACHABLE_EXEMPT).
        ("os.mkdir", "Controller.private_dir"), ("os.chmod", "Controller.private_dir"),
        ("os.mkdir", "Controller.stage_deployment"), ("os.chmod", "Controller.stage_deployment"),
        ("_write_private_file", "Controller.stage_deployment"), ("write_private_json", "Controller.stage_deployment"),
        ("_write_private_file", "Controller._seal_compose"), ("_write_private_file", "Controller._seal_config"),
        ("_write_private_file", "Controller.seal_deployment"), 
        (".mkdir", "Controller._capture"), (".open", "Controller.dump_store"), ("os.replace", "Controller.dump_store"),
        (".open", "Controller.write_dump_list"), ("_write_private_file", "Controller.write_manifest"),
        ("_write_private_file", "Controller.write_verification"), ("write_private_json", "Controller._record_capture"),
        (".mkdir", "Controller._purge_bundle"), (".open", "Controller._purge_bundle"),
        ("_write_private_file", "Controller._purge_bundle"), ("write_private_json", "Controller._purge_bundle"),
        ("os.replace", "Controller.restore_instance"), ("_write_private_file", "Controller._read_bundle_at"),
        # Audit AF-1: the runtime .env and the restored state files are written from their verified payload bytes
        # (restore-instance, under its journal), never copied from an unlisted bundle path.
        ("_write_private_file", "Controller.restore_runtime_environment"),
        # PF-A3.2 (sections 3.1-3.7), all inside the instance lock and an operation the gate admitted: the private
        # state directory (ensure_state_dir) and the revisions root of restore-instance; the private temporary trees
        # (the deployed tree a switch stages from, the restore bundle's workspace tree and .env bytes) and the two
        # bundle files removed from that private tree; the W1 stage tree (mkdir 0700 inside the generation
        # container) and the restore history tree; the purge bundle's folder; the frozen admin configuration copy
        # (0400); the effect writers (the seal record, the deployed pointer, the state files and last-reset of a
        # file-write effect) and the purge cleanup effects (backups, .env, state, the legacy marker, the reset admin
        # configuration). The pending.json writers (phase/pause/resume/finish_*) and replace_source* are gone.
        ("os.chmod", "Controller.ensure_state_dir"), (".mkdir", "Controller.ensure_state_dir"),
        (".mkdir", "Controller.ensure_revisions_root"),
        ("tempfile.mkdtemp", "Controller.deployed_tree"), ("tempfile.mkdtemp", "Controller.restore_workspace_tree"),
        ("tempfile.mkdtemp", "Controller.restore_env_bytes"), (".unlink", "Controller.restore_workspace_tree"),
        ("os.mkdir", "Controller.w1_stage"), ("os.mkdir", "Controller.history_tree"),
        (".mkdir", "Controller.capture_purge_bundle"), ("_write_private_file", "Controller.freeze_admin_config"),
        ("write_private_json", "Controller.act_seal"), ("write_json", "Controller.act_pointer"),
        ("write_json", "Controller.act_file"), ("_write_private_file", "Controller.act_file"),
        (".unlink", "Controller.purge_cleanup"), ("shutil.rmtree", "Controller.purge_cleanup"),
        # PF-A3.3 (sections 3.1-3.17), all inside the instance lock and an operation the gate admitted: the isolated
        # topology's private directory (operations/<op>/isolated/<project>, mkdir 0700 + chmod), its topology.json
        # (write_topology_record; set to removed by a cleanup's teardown) and data-checks.json (0600); the preflight's
        # private temporary render directory and its app.env, and the verification's private temporary source tree,
        # both inside the operation directory; the app-check-<label>.json summaries (counts only); and the
        # generation seal (its folder under backups/generations, seal.json 0600 and the archive's atomic rename).
        ("os.mkdir", "Controller.topology_directory"), ("os.chmod", "Controller.topology_directory"),
        ("_write_private_file", "Controller.write_topology_record"),
        ("_write_private_file", "Controller.cleanup_delete"),
        ("_write_private_file", "Controller.act_topology_stores"),
        ("tempfile.mkdtemp", "Controller.isolation_preflight"),
        ("_write_private_file", "Controller.isolation_preflight"),
        ("tempfile.mkdtemp", "Controller.payload_checks"),
        ("write_private_json", "Controller.app_invariants"),
        (".mkdir", "Controller.seal_generation"), ("_write_private_file", "Controller.seal_generation"),
        ("os.replace", "Controller.seal_generation"),
    },
    "pf_instance.py": {
        ("_write_private_file", "_stage_instance_dir"), ("_write_private_file", "_write_registry"),
        ("_write_private_file", "_write_reservation"), ("_write_private_file", "initialize_installation_root"),
        ("os.chmod", "_create_lock_file"), ("os.chmod", "_create_private_dir"), ("os.chmod", "_write_private_file"),
        ("os.chmod", "initialize_installation_root"), ("os.mkdir", "_create_private_dir"),
        ("os.rename", "_publish_instance_dir"), ("os.replace", "_write_private_file"),
        # PF-A3.1: the seal of a deployment staging directory (rename relative to the held parent descriptor).
        ("os.rename", "publish_private_dir"),
        # PF-A2.3: the one metadata engine (section 3.9): fchown then fchmod of a descriptor opened no-follow relative
        # to its parent, after the identity, link-count, before-state and ACL checks.
        ("os.fchown", "apply_entry_target"), ("os.fchmod", "apply_entry_target"),
        # PF-A3.2 operation store (section 3.1): journal generations and the private lists (attempts, children,
        # deletion progress) through the one private-file writer; the generation container (mkdir 0700, fchmod of
        # the held descriptor) and the noreplace rename relative to held directory descriptors (W2/W3).
        ("_write_private_file", "write_journal_generation"), ("_write_private_file", "rewrite_private_list"),
        ("os.mkdir", "create_generation_container"), ("os.fchmod", "create_generation_container"),
        ("os.rename", "rename_noreplace_at"),
        # PF-A3.3: the write-once private files of an operation (exclusive create, then fchmod of the held descriptor:
        # the topology app.env/compose.json, a write-once deletion plan, confirmation-summary.txt and
        # acknowledgement-<hash12>.json) and the registry tombstone/claim (write_record_state: the record through
        # the one private-file writer, under the registry lock, OD-A33-08).
        ("os.fchmod", "write_once"), ("_write_private_file", "write_record_state"),
    },
    "pf_bootstrap.py": set(),
    "pf_runner.py": {("open", "ProcessRunner.run"), ("os.replace", "ProcessRunner._record_effect")},
    "pf_config.py": {("_write_private", "freeze_app_config"), ("os.chmod", "_write_private"),
                     ("os.replace", "_write_private")},
    "pf_source.py": {
        (".open", "archive_verified_tree"), ("os.chmod", "SourceStore.create"), ("os.chmod", "SourceStore.export"),
        ("os.mkdir", "SourceStore.create"), ("os.mkdir", "SourceStore.export"), ("os.replace", "write_manifest"),
        ("shutil.rmtree", "SourceStore.create"), ("tempfile.TemporaryDirectory", "SourceStore.create"),
        ("tempfile.mkstemp", "SourceStore.export"),
        # PF-A3.1: the fd-safe archive writer (an exclusive 0600 destination) and the descriptor-relative extractor
        # (a new private directory, its 0700 parents and the exact mode of each exclusively created file).
        (".open", "archive_tree"), ("os.mkdir", "extract_archive"), ("os.mkdir", "_open_created"),
        ("os.fchmod", "_extract_into"),
    },
    "pf_docker.py": set(),
    # PF-A2.1: the journal writer, the staged release, smoke evidence, binding writes and restores, and the three
    # renames (atomic intent, release publication, root publication). The wider installer tracker below adds links,
    # unlinks and the private-directory helpers.
    "pf_install.py": {
        ("_write_private_file", "_Run._apply_smoke"), ("_write_private_file", "_Run._apply_stage_release"),
        ("_write_private_file", "_Run._write_binding"), ("_write_private_file", "_Run.restore_bindings"),
        ("_write_private_file", "_journal_write"), ("os.rename", "_Run._apply_publish_release"),
        ("os.rename", "_Run._apply_publish_root"), ("os.rename", "_Run.cancel"), ("os.rename", "_Run.write_intent"),
        # PF-A2.3 vocabulary only (fchmod/fchown now tracked): the launcher temp and the staged legacy copy (PF-A2.1).
        ("os.fchmod", "_Run._apply_bind_launcher"), ("os.fchmod", "_Run._apply_stage_legacy_file"),
        ("os.fchown", "_Run._apply_stage_legacy_file"),
    },
}
# PF-A2.1 (SS-3 for pf_install.py): every call that can change the filesystem, with the wider installer vocabulary
# (section 4.2: _write_private_file, _create_private_dir, _create_lock_file, _remove_tree, register_instance,
# discard_unused_registration, os.rename, the exclusive os.open of a staged copy or launcher temp, and os.link).
INSTALL_WRITERS = {"_journal_write", "_write_private_file", "_create_private_dir", "_create_lock_file", "_remove_tree",
                   "register_instance", "discard_unused_registration", "initialize_installation_root"}
INSTALL_OS_WRITES = {"link", "unlink", "rmdir", "rename", "replace", "mkdir", "chmod", "chown", "fchmod", "fchown",
                     "write"}
INSTALL_WRITE_SITES = {
    ("_create_lock_file", "_Run.lock"), ("_create_private_dir", "_Run._apply_smoke"),
    ("_create_private_dir", "_Run._apply_stage_release"), ("_create_private_dir", "_Run.lock_build"),
    ("_create_private_dir", "_Run.verify_problems"), ("_create_private_dir", "_Run.write_intent"),
    ("_journal_write", "_Run.save"), ("_journal_write", "_Run.write_intent"), ("_remove_tree", "_Run._remove_build"),
    ("_remove_tree", "_Run.abandon"), ("_remove_tree", "_Run.cancel"), ("_remove_tree", "_Run.lock_build"),
    ("_remove_tree", "_remove_intent_leftovers"), ("_remove_tree", "_remove_own_leftover"),
    ("_write_private_file", "_Run._apply_smoke"), ("_write_private_file", "_Run._apply_stage_release"),
    ("_write_private_file", "_Run._write_binding"), ("_write_private_file", "_Run.restore_bindings"),
    ("_write_private_file", "_journal_write"), ("discard_unused_registration", "_Run._discard_registration"),
    ("initialize_installation_root", "_Run._apply_build_root"), ("os.fchmod", "_Run._apply_bind_launcher"),
    ("os.fchmod", "_Run._apply_stage_legacy_file"), ("os.fchown", "_Run._apply_stage_legacy_file"),
    ("os.link", "_Run._apply_bind_launcher"), ("os.link", "_Run._apply_publish_legacy_file"),
    ("os.open(O_CREAT)", "_Run._apply_bind_launcher"), ("os.open(O_CREAT)", "_Run._apply_stage_legacy_file"),
    ("os.rename", "_Run._apply_publish_release"), ("os.rename", "_Run._apply_publish_root"),
    ("os.rename", "_Run.cancel"), ("os.rename", "_Run.write_intent"), ("os.rmdir", "_Run._remove_build"),
    ("os.rmdir", "_remove_own_leftover"), ("os.unlink", "_Run._after_observed_complete"),
    ("os.unlink", "_Run._apply_bind_launcher"), ("os.unlink", "_Run._remove_own_launcher_temp"),
    ("os.unlink", "_Run._apply_publish_legacy_file"), ("os.unlink", "_Run._reconcile"),
    ("os.unlink", "_Run._remove_build"), ("os.unlink", "_Run._remove_legacy_copies"), ("os.unlink", "_Run.abandon"),
    ("os.unlink", "_Run.restore_bindings"), ("os.unlink", "_remove_own_leftover"),
    ("os.write", "_Run._apply_bind_launcher"), ("os.write", "_Run._apply_stage_legacy_file"),
    ("register_instance", "_Run._apply_register_instance"),
}
# Read-only installer entries: `pf install status`, every preflight, the gate and the displays.
INSTALL_READ_ONLY_ROOTS = ("_status", "preflight", "_preflight", "operations", "pending_operations",
                           "require_no_pending_install", "describe_installation", "read_candidate",
                           "read_retained_release", "classify_launcher", "launcher_prefix", "render_next",
                           "load_operation", "render_summary", "confirmation_phrase")


def install_write(call):
    """The SS-3 name of a pf_install.py call that can change the filesystem, or None."""
    name = tracked_write(call)
    if name:
        return name
    target = dotted(call.func) or ""
    last = target.rsplit(".", 1)[-1]
    if last in INSTALL_WRITERS:
        return last
    if target.startswith("os.") and last in INSTALL_OS_WRITES:
        return target
    if target == "os.open" and "O_CREAT" in ast.dump(call):
        return "os.open(O_CREAT)"
    # PF-A2.3 audit: a write-mode open of an existing file (no O_CREAT) is a write site too.
    if target == "os.open" and any(flag in ast.dump(call) for flag in ("O_WRONLY", "O_RDWR", "O_TRUNC", "O_APPEND")):
        return "os.open(write)"
    return None
# Read-only roots of SS-3 rule 2 (Controller methods by name or prefix, plus module functions).
READ_ONLY_ROOTS = ("__init__", "status", "doctor", "snapshots", "recoveries", "verify_recovery", "compose_ps",
                   "compose_logs", "policy_permits", "permissions_check", "permissions_plan")
READ_ONLY_ROOT_PREFIXES = ("display_", "log_", "describe_")
READ_ONLY_MODULE_ROOTS = ("classify_command", "unattended")
# Write sites a read-only route can reach statically, each with the guard that keeps it inert there.
READ_ONLY_REACHABLE_EXEMPT = {
    ("_write_private", "Controller.write_private_json"):
        "returns None when self.operation_dir is None, which read-only routes never set (only lock() does)",
    ("write_private_json", "Controller._record_daemon"):
        "returns when self.operation_dir is None; write_private_json is itself a no-op outside an operation",
    ("write_private_json", "Controller._append_envelope_record"):
        "returns when self.operation_dir is None (doctor's envelope render records nothing)",
    ("write_private_json", "Controller.require_topology_owned"):
        "reached only from command() for an effect-carrying child inside an operation (operation_dir set); "
        "outside one write_private_json is a no-op and effect children are refused",
    ("_write_private_file", "Controller._read_bundle_at"):
        "PF-A3.1: the legacy migration record and migrated bytes are written only when self.operation_dir is set; "
        "the read-only listings migrate in memory and write nothing",
}


# ============================================================================ fixtures


class Base(unittest.TestCase):
    """A protected installation with one registered instance ``staging`` and the fake daemon."""

    project = PROJECT

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.layout = pfx.install_root(self.base)
        self.fake = pfx.install_fake_docker(self.layout)
        self.context, self.paths = self.instance("staging", self.project)
        self.state(pfx.owned_topology(self.context))

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

    def run_main(self, arguments, *, terminal=None, interactive=False, trusted_launch=True):
        """In-process CLI. ``terminal`` replaces sys.stdin with a stub (True/False/None = unchanged);
        ``interactive`` is the interactive harness (an operator at a terminal)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            if terminal is not None:
                stack.enter_context(mock.patch.object(sys, "stdin", Terminal(terminal)))
            if interactive:
                stack.enter_context(mock.patch.object(pf, "unattended", return_value=False))
            code = pf.main(arguments, installation_root=self.layout.root, running_release=self.layout.release_dir,
                           trusted_launch=trusted_launch)
        return code, stdout.getvalue(), stderr.getvalue()

    def operation_dirs(self, context=None):
        root = (context or self.context).operations_dir
        return sorted(os.listdir(str(root))) if root.exists() else []

    def assert_lock_free(self, context=None):
        handle = pf_instance.acquire_instance_lock(context or self.context)
        handle.release()

    def identities(self):
        """Container, volume, network and image identities on the fake daemon (A1-T11 style evidence)."""
        state = self.fake.state()
        return {
            "containers": sorted([item["id"], item["name"], item.get("status"),
                                  json.dumps(item.get("labels"), sort_keys=True)] for item in state["containers"]),
            "volumes": sorted([item["name"], json.dumps(item.get("labels"), sort_keys=True)]
                              for item in state["volumes"]),
            "networks": sorted([item["id"], item["name"]] for item in state["networks"]),
            "images": sorted([item["id"], sorted(item["repo_tags"])] for item in state["images"]),
        }

    def verbs(self):
        result = []
        for argv in self.fake.argvs():
            if argv[:1] == ["compose"]:
                rest = argv[1:]
                while rest and rest[0].startswith("-"):
                    rest = rest[2:]
                result.append(rest)
        return result

    def compose_prefix(self, context=None):
        context = context or self.context
        return ["compose", "--project-directory", str(context.paths.workspace), "--env-file",
                str(context.diagnostic_env_path), "-p", context.compose_project, "-f",
                str(context.control.path / "compose.nas.yaml")]

    def write_journal(self):
        """PF-A3.2: an interrupted deploy as a real plan and journal (the former pending.json): staged and the
        database service started, the migration not started and nothing unresolved, so a resume forwards (section
        3.6) without observing first. Returns the operation files' bytes."""
        self.journal_plan = pfx.frozen_operation(self.context, "deploy", phase="initializing",
                                                 states={"e0001": "complete", "e0002": "complete"})
        return pfx.operations_bytes(self.context)

    def oneoff_exited(self, container_id):
        """An owned one-off that is not running (the section 3.6 probe would refuse a resume next to a running one)."""
        state = self.fake.state()
        for item in state["containers"]:
            if item["id"] == container_id:
                item["status"] = "exited"
        self.fake.write_state(state)

    def set_config(self, **values):
        path = self.paths["configuration"] / "pf-config.json"
        path.write_text(json.dumps(dict(json.loads(path.read_text()), **values)) + "\n")


# ============================================================================ DT: dispatch tables


class DispatchTables(unittest.TestCase):
    """DT-1..DT-9: DISPATCH drives main(); ENTRY_ROUTES names every non-CLI route and its test."""

    def subcommands(self):
        action = next(item for item in pf.parser()._actions if isinstance(item, argparse._SubParsersAction))
        return action.choices

    def test_dt1_dispatch_equals_the_parser_and_read_only_set(self):
        # PF-A2.3: `permissions` is one parser word whose verbs are three DISPATCH rows.
        self.assertEqual({name.split(" ", 1)[0] for name in pf.DISPATCH}, set(self.subcommands()))
        verbs = next(item for item in self.subcommands()["permissions"]._actions
                     if isinstance(item, argparse._SubParsersAction)).choices
        self.assertEqual({"permissions " + verb for verb in verbs},
                         {name for name in pf.DISPATCH if name.startswith("permissions ")})
        self.assertEqual(pf.KNOWN_COMMANDS, frozenset(name.split(" ", 1)[0] for name in pf.DISPATCH))
        self.assertEqual(pf.READ_ONLY_COMMANDS,
                         {"instances", "status", "doctor", "backups", "recoveries", "ps", "logs", "permissions check",
                          "permissions plan"})
        self.assertEqual(len(pf.DISPATCH), 24)  # PF-A3.1: "backup emergency"; PF-A3.3: "cleanup"
        self.assertNotIn("install", pf.READ_ONLY_COMMANDS)

    def test_dt2_every_field_is_in_its_token_set(self):
        for name, route in pf.DISPATCH.items():
            with self.subTest(route=name):
                self.assertEqual(route.name, name)
                self.assertIn(route.mutability, ("read-only", "registry-read", "mutating", "conditional",
                                                 "installation"))
                self.assertIn(route.pending, ("any", "refuse", name))
                self.assertIn(route.preflight, ("none", "owned", "empty-target", "plan", "restore", "apply"))
                self.assertIn(route.fail_closed, ("never", "always", "unless-side-by-side", "if-apply"))
                self.assertIn(route.unattended, ("allowed", "terminal", "policy"))
                self.assertIn(route.policy_class, ("", "backup", "release-check"))
                if route.lock:
                    self.assertTrue(route.trusted_launch and route.trusted_context)
                    self.assertIn(route.unattended, ("terminal", "policy"))
                elif name == "install":
                    # PF-A2.1: the one unlocked route that writes; it takes its own locks and journal in pf_install
                    # and needs a terminal and a trusted launch for every verb except `install status` (DT-2b).
                    self.assertEqual((route.pending, route.preflight, route.fail_closed, route.unattended),
                                     ("any", "none", "never", "terminal"))
                    self.assertEqual((route.mutability, route.handler, route.trusted_launch, route.trusted_context),
                                     ("installation", "pf_install.run_installed", True, False))
                else:
                    self.assertEqual((route.pending, route.preflight, route.fail_closed, route.unattended),
                                     ("any", "none", "never", "allowed"))
                    self.assertIn(route.mutability, ("read-only", "registry-read"))
                    if name.startswith("permissions "):
                        # PF-A2.3: trusted launch (they read protected state as root), not trusted context (they
                        # map the refuse findings onto scopes themselves).
                        self.assertEqual((route.trusted_launch, route.trusted_context), (True, False))
                self.assertEqual(bool(route.policy_class), route.unattended == "policy")
        self.assertEqual({name for name, route in pf.DISPATCH.items() if route.unattended == "policy"},
                         {"backup", "release-check"})

    def test_dt3_pending_routes_agree_with_the_table(self):
        self.assertTrue(set(pf.PENDING_ROUTES) <= set(pf.DISPATCH))
        self.assertFalse([key for key in pf.PENDING_ROUTES if ":" in key])
        locked = [name for name, route in pf.DISPATCH.items() if route.lock]
        for name in locked:
            route = pf.DISPATCH[name]
            self.assertEqual(route.pending == "refuse", name not in pf.PENDING_ROUTES, name)
            if name in pf.PENDING_ROUTES:
                self.assertEqual(route.pending, name)
        # PF-A3.2 (section 6.2 DT-3): the former pending.json dicts become OperationView fixtures of real plans and
        # journals; the predicates are the section 3.3 gate. The former six rows keep their accepted sets (`resume`
        # now re-enters every non-terminal journal), plus the alias rows and the pending-switch and interval rows.
        context = fixture_context()
        complete = lambda *ids: {item: "complete" for item in ids}  # noqa: E731
        rows = {
            "deploy/initializing": (pfx.lifecycle_plan(context, "deploy"), "initializing",
                                    dict(complete("e0001", "e0002"), e0003="unknown"),
                                    {"resume", "abort-deploy", "backup emergency"}),
            "update/preserving": (pfx.lifecycle_plan(context, "update"), "preserving",
                                  dict(complete("e0001", "e0002"), e0003="unknown"),
                                  {"resume", "rollback", "backup emergency"}),
            "purge/deleting": (pfx.lifecycle_plan(context, "purge"), "deleting",
                               dict(complete("e0001", "e0002", "e0003", "e0004"), e0005="unknown"),
                               {"resume", "purge"}, {"plan_sha256": "d" * 64, "delete_backups": True,
                                                     "reset_admin_config": False,
                                                     "confirmed_at": "20261007T040500Z"}),
            "rollback/activating": (pfx.lifecycle_plan(context, "rollback"), "activating",
                                    dict(complete("e0001", "e0002", "e0003", "e0004", "e0005"), e0006="unknown"),
                                    {"resume", "rollback", "backup emergency"}),
            "backup/capturing": (pfx.lifecycle_plan(context, "backup"), "capturing", {"e0001": "unknown"},
                                 {"resume", "backup"}),
            "restore-instance/preparing-target": (pfx.lifecycle_plan(context, "restore-instance"), "preparing-target",
                                                  dict(complete("e0001"), e0002="unknown"),
                                                  {"resume", "restore-instance"}),
            "update/workspace_sync_pending": (pfx.lifecycle_plan(context, "update"), "workspace_sync_pending",
                                              complete(*[f"e{index:04d}" for index in range(1, 12)]),
                                              {"resume", "rollback", "backup", "backup emergency"}),
            "update/syncing-workspace (interval)": (pfx.lifecycle_plan(context, "update"), "syncing-workspace",
                                                    dict(complete(*[f"e{index:04d}" for index in range(1, 14)]),
                                                         e0014="unknown"), {"resume"}),
        }
        own = {name for name in locked if pf.DISPATCH[name].pending == name}
        views = {label: pf.OperationView(row[0]["kind"], row[1], row[0], pfx.lifecycle_journal(
            row[0], phase=row[1], states=row[2], deletion=row[4] if len(row) > 4 else None))
                 for label, row in rows.items()}
        expected = {label: row[3] for label, row in rows.items()}
        for phase in ("interrupted", "applying"):
            views["permissions/" + phase] = pf.OperationView("permissions", phase)
            expected["permissions/" + phase] = {"permissions apply"}
        for label, view in views.items():
            with self.subTest(journal=label):
                accepted = {name for name in locked if name in pf.PENDING_ROUTES and pf.PENDING_ROUTES[name][0](view)}
                self.assertEqual(accepted, expected[label])
                self.assertTrue(accepted <= own)

    def test_dt4_every_handler_resolves(self):
        for name, route in pf.DISPATCH.items():
            with self.subTest(route=name):
                owner, _, attribute = route.handler.rpartition(".")
                self.assertIn(owner, ("Controller", "", "pf_install"))
                target = {"Controller": pf.Controller, "": pf, "pf_install": pf.pf_install}[owner]
                self.assertTrue(callable(getattr(target, attribute)))

    def test_dt5_main_compares_each_known_command_exactly_once(self):
        source, tree = parse_module("pf-admin.py")
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        compared = []
        for node in ast.walk(main):
            # PF-A2.3: the `permissions` verbs are dispatched by their route name.
            if isinstance(node, ast.Compare) and dotted(node.left) in ("args.command", "route.name") \
                    and all(isinstance(value, ast.Constant) for value in node.comparators):
                for operator, value in zip(node.ops, node.comparators):
                    self.assertIsInstance(operator, ast.Eq, ast.dump(node))
                    compared.append(value.value)
        self.assertEqual(sorted(compared), sorted(pf.DISPATCH))
        self.assertEqual(len(compared), len(set(compared)))
        self.assertNotIn("passthrough", ast.get_source_segment(source, main))
        membership = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) \
                    and any(dotted(value) in ("KNOWN_COMMANDS", "DISPATCH") for value in node.comparators):
                membership.append(node.lineno)
        classify = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "classify_command")
        self.assertTrue(membership)
        self.assertTrue(all(classify.lineno <= line <= classify.end_lineno for line in membership), membership)

    def test_dt6_preflight_and_fail_closed_follow_the_table(self):
        owned = {"backup", "backup emergency", "update", "rollback", "reset-db", "resume"}
        for name, route in pf.DISPATCH.items():
            for side_by_side in (False, True):
                for apply in (False, True):
                    args = types.SimpleNamespace(side_by_side=side_by_side, apply=apply)
                    with self.subTest(route=name, side_by_side=side_by_side, apply=apply):
                        expected = "owned" if (name in owned or (name == "restore-instance" and side_by_side)
                                               or (name == "release-check" and apply)) else "none"
                        self.assertEqual(pf.route_preflight(route, args), expected)
                        fail_closed = {"always": True, "never": False, "unless-side-by-side": not side_by_side,
                                       "if-apply": apply}[route.fail_closed]
                        self.assertEqual(pf.route_fail_closed(route, args), fail_closed)
        for name in ("deploy", "purge", "abort-deploy"):
            self.assertEqual(pf.route_preflight(pf.DISPATCH[name], types.SimpleNamespace()), "none")

    def test_dt7_removed_words_cover_every_former_forwarded_word(self):
        removed = set(pf.REMOVED_COMPOSE_ROUTES)
        self.assertFalse(removed & set(pf.DISPATCH))
        self.assertFalse(removed & pf.COMPOSE_GLOBAL_OPTIONS)
        self.assertTrue(FORMER_MUTATING_VERBS <= removed)
        self.assertTrue(FORMER_READ_ONLY_VERBS - {"ps", "logs"} <= removed)
        # PF-A2.2: `config` is the wizard group now; its former Compose guidance still answers `pf config` without
        # admin/app (DT-3), so the guidance group stays while the word leaves the removed-route table.
        self.assertNotIn("config", removed)
        self.assertIn("config", pf.DISPATCH)
        self.assertIn("config", pf.REMOVED_ROUTE_GUIDANCE)
        self.assertTrue(pf_docker.ENVELOPE_VERBS <= removed)
        self.assertEqual(set(pf.REMOVED_COMPOSE_ROUTES.values()) | {"config"}, set(pf.REMOVED_ROUTE_GUIDANCE))
        self.assertEqual(pf.COMPOSE_READ_ONLY_VERBS, FORMER_READ_ONLY_VERBS)

    def test_dt8_entry_routes_are_consistent_and_each_names_a_real_test(self):
        ids = [entry.id for entry in pf.ENTRY_ROUTES]
        self.assertEqual(ids, ["E" + str(number) for number in range(1, 12)])
        loader = unittest.TestLoader()
        for entry in pf.ENTRY_ROUTES:
            with self.subTest(entry=entry.id):
                target = entry.target_route
                self.assertTrue(target in pf.DISPATCH or target in ("*", "refuse", "fail_closed", "installer-init"),
                                target)
                if target in pf.DISPATCH:
                    route = pf.DISPATCH[target]
                    self.assertEqual(entry.mutability, route.mutability)
                    self.assertEqual(entry.lock, "yes" if route.lock else "no")
                    self.assertEqual(entry.pending, route.pending)
                elif target == "refuse":
                    self.assertEqual((entry.mutability, entry.lock, entry.pending), ("none", "none", "n/a"))
                elif target == "fail_closed":
                    self.assertEqual((entry.mutability, entry.lock, entry.pending),
                                     ("mutating", "held", "existing-journal"))
                elif target == "installer-init":
                    # PF-A2.1 E11: the repository installer initializes a new root under its own build lock and
                    # install journal; no instance lock exists yet.
                    self.assertEqual((entry.mutability, entry.lock, entry.pending), ("mutating", "none", "install-journal"))
                    self.assertEqual(entry.owner, "PF-A2.1")
                else:
                    self.assertEqual((entry.mutability, entry.lock, entry.pending),
                                     ("per-route", "per-route", "per-route"))
                tests = list(loader.loadTestsFromName(entry.test))
                self.assertEqual(len(tests), 1, entry.test)
                self.assertEqual(tests[0].id().rsplit(".", 1)[-1], entry.test.rsplit(".", 1)[-1])
                self.assertNotIn("_FailedTest", type(tests[0]).__name__)
                if target == "fail_closed":
                    # PF-A1.4 audit A14-AUD-03: the proving test must reach fail_closed, either directly or
                    # through a route whose failures always run it (a `backup` signal test never does).
                    method = getattr(tests[0], tests[0]._testMethodName)
                    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
                    calls = {node.func.attr for node in ast.walk(tree)
                             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
                    words = {node.value for node in ast.walk(tree)
                             if isinstance(node, ast.Constant) and isinstance(node.value, str)}
                    always = {name for name, route in pf.DISPATCH.items() if route.fail_closed == "always"}
                    self.assertTrue("fail_closed" in calls or words & always, entry.test)

    def test_dt9_every_terminal_route_reaches_a_typed_confirmation(self):
        _, tree = parse_module("pf-admin.py")
        controller = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Controller")
        methods = {node.name: node for node in controller.body if isinstance(node, ast.FunctionDef)}

        def confirms(name, word="confirm"):
            seen, todo = set(), [name]
            while todo:
                current = todo.pop()
                if current in seen or current not in methods:
                    continue
                seen.add(current)
                for node in ast.walk(methods[current]):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == word:
                        return True
                    if isinstance(node, ast.Call) and dotted(node.func) and dotted(node.func).startswith("self."):
                        todo.append(dotted(node.func)[5:])
            return False

        terminal = [route for route in pf.DISPATCH.values() if route.unattended == "terminal" and route.lock]
        # PF-A2.1: the unlocked install route asks its typed confirmations inside pf_install.
        install = pf.DISPATCH["install"]
        self.assertEqual((install.unattended, install.lock), ("terminal", False))
        self.assertIn("_confirm(interaction, confirmation_phrase(result.plan))",
                      inspect.getsource(pf.pf_install.run_installed))
        self.assertIn('_confirm(interaction, f"{verb} {_op8(operation_id)}")', inspect.getsource(pf.pf_install.resume))
        self.assertEqual({route.name for route in terminal},
                         {"deploy", "abort-deploy", "purge", "restore-instance", "reset-db", "rollback", "resume",
                          "update", "config", "permissions apply", "backup emergency", "cleanup"})
        for route in terminal:
            with self.subTest(route=route.name):
                # PF-A2.2 (OD-A22-17): the config wizards edit proposal files and confirm with [y/N] (confirm_write);
                # the typed phrases stay for protected-state changes. PF-A2.3: `permissions apply` (and its --resume
                # and --abandon) confirms with its own typed phrase (permission_confirm: cancel copy on EOF/Ctrl-C).
                word = {"config": "confirm_write", "permissions apply": "permission_confirm"}.get(route.name, "confirm")
                self.assertTrue(confirms(route.handler.split(".", 1)[1], word), route.handler)

    def test_dt5b_only_the_config_route_skips_the_configuration_load_and_the_freeze(self):
        """PF-A2.2 DT-5: main() passes load_config=False and freeze=False for `config` only."""
        source, tree = parse_module("pf-admin.py")
        calls = {}
        for call, where in qualified_calls(tree):
            target = dotted(call.func) or ""
            for name, keyword in (("require_trusted_context", "load_config"), ("lock", "freeze"),
                                  ("begin_operation", "freeze")):
                if target.endswith("." + name):
                    for item in call.keywords:
                        if item.arg == keyword:
                            calls.setdefault(name, []).append((where, ast.get_source_segment(source, item.value)))
        # PF-A2.3: `permissions apply` joins `config` (NO_CONFIG_ROUTES); the read-only verbs never reach either call.
        self.assertEqual(calls["require_trusted_context"], [("main", "route.name not in NO_CONFIG_ROUTES")])
        self.assertEqual(calls["lock"], [("main", "route.name not in NO_CONFIG_ROUTES")])
        self.assertEqual(pf.NO_CONFIG_ROUTES, frozenset({"config", "permissions apply"}))
        self.assertEqual(calls["begin_operation"], [("Controller.lock", "freeze")])
        route = pf.DISPATCH["config"]
        self.assertEqual((route.lock, route.trusted_launch, route.trusted_context, route.pending, route.preflight,
                          route.fail_closed, route.unattended, route.policy_class, route.handler),
                         (True, True, True, "refuse", "none", "never", "terminal", "", "Controller.configure"))

    @ROOT_REQUIRED
    def test_e8_verifier_refuses_outside_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            os.chmod(base, 0o755)
            layout = pfx.install_root(base)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            copied = elsewhere / pf_instance.BOOTSTRAP_MODULE_NAME
            shutil.copy2(layout.bootstrap_module, copied)
            result = subprocess.run([os.path.realpath(sys.executable), "-I", "-B", str(copied),
                                     pf_instance.ROOT_HANDSHAKE_OPTION, str(layout.root), "status"],
                                    env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, check=False, timeout=120)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("bootstrap-module-location", result.stderr)
            self.assertIn("No control code was executed", result.stderr)


# ============================================================================ RC: removed routes


@ROOT_REQUIRED
class RemovedRoutes(Base):
    """A1-T15: every former catch-all word and option is a named refusal before anything is read."""

    def assert_refused_before_anything(self, cases, prefix):
        before = pfx.snapshot_tree(self.base)
        state_before = self.fake.state_bytes()
        with mock.patch.object(pf.pf_instance, "load_registry", wraps=pf.pf_instance.load_registry) as registry:
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    code, out, err = self.run_main(arguments, interactive=True)
                    self.assertEqual(code, 1, err)
                    self.assertTrue(err.startswith("ERROR: " + prefix), err)
                    self.assertIn(expected, err)
                    self.assertIn("Nothing was read or changed.", err)
        registry.assert_not_called()
        self.assertEqual(self.fake.calls(), [])
        self.assertEqual(self.operation_dirs(), [])
        self.assertEqual(self.fake.state_bytes(), state_before)
        self.assertEqual(pfx.snapshot_tree(self.base), before)

    def test_rc1_every_removed_word_is_refused_with_its_guidance(self):
        hostile = ["-v", "--privileged", "--cap-add=SYS_ADMIN", "-v", "/:/host"]
        cases = []
        for word, group in sorted(pf.REMOVED_COMPOSE_ROUTES.items()):
            expected = f"compose-route-removed: 'pf {word}' no longer forwards to Docker Compose. " \
                       + pf.REMOVED_ROUTE_GUIDANCE[group]
            for prefix in ([], ["--instance", "staging"]):
                for tail in ([], hostile):
                    cases.append((prefix + [word] + tail, expected))
        self.assert_refused_before_anything(cases, "compose-route-removed: ")

    def test_rc2_compose_and_docker_global_options_are_refused(self):
        cases = [(arguments, f"compose-override-refused: '{option}' is a Compose or Docker global option")
                 for arguments, option in (
                     (["-f", "x", "ps"], "-f"), (["-p", "other", "ps"], "-p"),
                     (["--env-file", "/tmp/e", "ps"], "--env-file"),
                     (["--project-directory", "/tmp", "ps"], "--project-directory"),
                     (["--profile", "x", "ps"], "--profile"), (["--file=x", "ps"], "--file"),
                     (["-H", "tcp://x", "ps"], "-H"), (["--context", "x", "ps"], "--context"),
                     (["-fx", "ps"], "-f"))]
        self.assert_refused_before_anything(cases, "compose-override-refused: ")

    def test_rc3_unknown_word_is_refused(self):
        self.assert_refused_before_anything([(["frobnicate", "--now"], "unknown-command: Unknown command 'frobnicate'. "
                                              "Managed commands: " + ", ".join(sorted(pf.KNOWN_COMMANDS)))],
                                            "unknown-command: ")

    def test_rc4_raw_compose_config_and_its_output_file_are_refused(self):
        dump = self.base / "dump.yaml"
        self.assert_refused_before_anything(
            [(["config"], "not available through this route"),
             (["config", "-o", str(dump)], "not available through this route")], "compose-route-removed: ")
        self.assertFalse(dump.exists())

    def test_rc5_abbreviated_and_unknown_options_are_refused(self):
        self.assert_refused_before_anything(
            [(["--inst", "staging", "status"], "unknown-option: '--inst' is not a pf option"),
             (["--instanc=staging", "status"], "unknown-option: '--instanc' is not a pf option"),
             (["--yes", "backup"], "unknown-option: '--yes' is not a pf option")], "unknown-option: ")
        usage = io.StringIO()
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(usage), \
                mock.patch.object(pf, "unattended", return_value=False):
            pf.main(["--instance", "staging", "update", "--allow", "--latest"], installation_root=self.layout.root,
                    running_release=self.layout.release_dir, trusted_launch=True)
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("unrecognized arguments: --allow", usage.getvalue())
        self.assertEqual(self.fake.calls(), [])
        self.assertEqual(self.operation_dirs(), [])


@ROOT_REQUIRED
class ConfigRoute(Base):
    """PF-A2.2: the former Compose alias `config` is the wizard group (DT-3, DT-5 behaviour)."""

    def test_dt3_config_without_admin_or_app_keeps_the_removed_route_refusal(self):
        before = pfx.snapshot_tree(self.base)
        expected = ("compose-route-removed: 'pf config' without 'admin' or 'app' no longer forwards to Docker Compose. "
                    + pf.REMOVED_ROUTE_GUIDANCE["config"] + " Use 'pf config admin' or 'pf config app'. Nothing was read "
                    "or changed.")
        with mock.patch.object(pf.pf_instance, "load_registry", wraps=pf.pf_instance.load_registry) as registry:
            for arguments in (["config"], ["config", "--services"], ["config", "foo"], ["--instance", "staging", "config"],
                              ["config", "-o", str(self.base / "dump.yaml")]):
                with self.subTest(arguments=arguments):
                    code, out, err = self.run_main(arguments, interactive=True)
                    self.assertEqual(code, 1, err)
                    self.assertEqual(err, "ERROR: " + expected + "\n")
        registry.assert_not_called()
        self.assertEqual((self.fake.calls(), self.operation_dirs()), ([], []))
        self.assertEqual(pfx.snapshot_tree(self.base), before)
        usage = io.StringIO()
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(usage):
            pf.main(["config", "--help"], installation_root=self.layout.root, running_release=self.layout.release_dir,
                    trusted_launch=True)
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("admin", usage.getvalue())

    def test_dt5_deploy_still_loads_the_configuration_before_the_lock(self):
        (self.paths["configuration"] / "pf-config.json").unlink()
        (self.context.state_dir / "deployed.json").unlink()
        code, out, err = self.run_main(["--instance", "staging", "deploy"], interactive=True)
        self.assertEqual(code, 1, out + err)
        self.assertIn("Runtime configuration is missing or unreadable", err)
        self.assertEqual((self.operation_dirs(), self.fake.calls()), ([], []))
        self.assert_lock_free()


# ============================================================================ RA: read-only views


@ROOT_REQUIRED
class ReadOnlyViews(Base):
    """`pf ps` and `pf logs`: rebuilt argv, validated context, daemon binding, redacted bounded stream."""

    def test_ra1_ps_is_one_compose_child_without_lock_or_operation(self):
        probes = []
        original = pf.Controller.compose

        def compose(controller, *args, **kwargs):
            pf_instance.acquire_instance_lock(controller.context).release()  # the lock is free during the call
            probes.append(args)
            return original(controller, *args, **kwargs)

        with mock.patch.object(pf.Controller, "compose", compose):
            code, out, err = self.run_main(["--instance", "staging", "ps"])
        self.assertEqual(code, 0, err)
        self.assertEqual(probes, [("ps",)])
        self.assertEqual(self.fake.argvs(), [PROBE, ["compose", "version"], self.compose_prefix() + ["ps"]])
        self.assertEqual(self.operation_dirs(), [])
        self.assert_lock_free()

    def test_ra2_ps_options_map_to_the_exact_argv_and_hostile_ones_are_refused(self):
        code, out, err = self.run_main(["--instance", "staging", "ps", "-a", "-q", "--services", "--status", "running",
                                        "--format", "json", "db", "backend"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.argvs()[-1], self.compose_prefix() + [
            "ps", "--all", "--quiet", "--services", "--status", "running", "--format", "json", "db", "backend"])
        for arguments in (["--format", "{{.Names}}"], ["--filter", "x"]):
            with self.subTest(arguments=arguments):
                self.fake.clear_calls()
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                    self.run_main(["--instance", "staging", "ps", *arguments])
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(self.fake.calls(), [])
        self.fake.clear_calls()
        code, out, err = self.run_main(["--instance", "staging", "ps", "nosuch"])
        self.assertEqual(code, 1)
        self.assertIn("unknown service 'nosuch'; services: db, backend, frontend", err)
        self.assertEqual(self.fake.calls(), [])

    def test_ra3_logs_options_map_to_the_exact_argv_with_their_bounds(self):
        timeouts = []
        original = pf.Controller.command

        def command(controller, argv, **kwargs):
            timeouts.append((argv[1:3], kwargs.get("timeout")))
            return original(controller, argv, **kwargs)

        with mock.patch.object(pf.Controller, "command", command):
            code, out, err = self.run_main(["--instance", "staging", "logs"])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.fake.argvs()[-1], self.compose_prefix() + ["logs", "--tail", "200"])
            self.assertEqual(timeouts[-1][1], pf.TIMEOUT_DIAGNOSTIC)
            code, out, err = self.run_main(["--instance", "staging", "logs", "-f", "-t", "--since", "30m", "--until",
                                            "2026-10-06T00:00:00Z", "backend"])
            self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.argvs()[-1], self.compose_prefix() + [
            "logs", "--tail", "200", "--follow", "--timestamps", "--since", "30m", "--until", "2026-10-06T00:00:00Z",
            "backend"])
        self.assertEqual(timeouts[-1][1], pf.TIMEOUT_LOGS_FOLLOW)
        for arguments in (["--tail", "0"], ["--tail", "10001"], ["--tail", "all"], ["--since", "yesterday"]):
            with self.subTest(arguments=arguments):
                self.fake.clear_calls()
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                    self.run_main(["--instance", "staging", "logs", *arguments])
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(self.fake.calls(), [])

    def test_ra4_log_output_is_redacted(self):
        secret = "p@ss:w/rd#1-logs"
        env_path = self.paths["configuration"] / ".env"
        env_path.write_text(pfx.ENV_TEXT.replace("POSTGRES_PASSWORD=abc123", "POSTGRES_PASSWORD='" + secret + "'"))
        encoded = urllib.parse.quote(secret, safe="")
        state = self.fake.state()
        state["compose"]["logs"] = f"backend-1 | connecting with {secret}\nbackend-1 | url postgresql://u:{encoded}@db\n"
        self.fake.write_state(state)
        code, out, err = self.run_main(["--instance", "staging", "logs", "backend"])
        self.assertEqual(code, 0, err)
        self.assertNotIn(secret, out + err)
        self.assertNotIn(encoded, out + err)
        self.assertIn(pf.pf_runner.REDACTED, out)

    def test_ra5_daemon_drift_refuses_both_views_before_any_compose_child(self):
        state = self.fake.state()
        state["info"] = pfx.daemon_info("OTHER-ENGINE-9999")
        self.fake.write_state(state)
        for view in ("ps", "logs"):
            with self.subTest(view=view):
                self.fake.clear_calls()
                code, out, err = self.run_main(["--instance", "staging", view])
                self.assertEqual(code, 1)
                self.assertIn("daemon-drift", err)
                self.assertEqual(self.fake.argvs(), [PROBE])

    def test_ra6_a_pending_journal_does_not_block_or_change(self):
        journal = self.write_journal()
        code, out, err = self.run_main(["--instance", "staging", "ps"])
        self.assertEqual(code, 0, err)
        self.assertEqual(pfx.operations_bytes(self.context), journal)

    def test_ra7_refused_context_or_untrusted_launch_starts_nothing(self):
        os.chmod(self.paths["workspace"].parent, 0o777)
        for view in ("ps", "logs"):
            with self.subTest(view=view):
                code, out, err = self.run_main(["--instance", "staging", view])
                self.assertEqual(code, 1)
                self.assertIn("ancestor-replaceable", err)
        os.chmod(self.paths["workspace"].parent, 0o755)
        code, out, err = self.run_main(["--instance", "staging", "ps"], trusted_launch=False)
        self.assertEqual(code, 1)
        self.assertIn("Compose views (ps, logs) must start through the installed bootstrap launcher", err)
        self.assertEqual(self.fake.calls(), [])

    def assert_bound_reached(self, arguments):
        code, out, err = self.run_main(["--instance", "staging", *arguments])
        self.assertEqual(code, 1, err)
        self.assertIn("ERROR: logs-bound-reached: 'pf logs' stopped at its bound", err)
        self.assertIn("Nothing was changed.", err)
        self.assertEqual(self.operation_dirs(), [])
        self.assertEqual(self.unresolved(), [])

    def unresolved(self):
        return [item for name in self.operation_dirs()
                for item in pf.pf_runner.load_unresolved_effects(self.context.operations_dir / name /
                                                                  "unresolved-effects.json")]

    def test_ra8_logs_bounds_end_with_a_named_outcome(self):
        state = self.fake.state()
        state["compose"].update(logs="backend-1 | started\n", logs_sleep=30)
        self.fake.write_state(state)
        with mock.patch.object(pf, "TIMEOUT_LOGS_FOLLOW", 1.0):
            self.assert_bound_reached(["logs", "-f", "backend"])
        state["compose"].update(logs="x" * 65536 + "\n", logs_sleep=30)
        self.fake.write_state(state)
        with mock.patch.object(pf.pf_runner, "STREAM_LIMIT", 4096):
            self.assert_bound_reached(["logs", "backend"])


# ============================================================================ US: unattended gate


@ROOT_REQUIRED
class UnattendedGate(Base):
    """ARCH section 5: without a terminal, a locked route needs an explicit instance and a protected grant."""

    def assert_nothing_started(self, journal=None):
        self.assertEqual(self.fake.calls(), [])
        self.assert_lock_free()
        self.assertEqual(pfx.operations_bytes(self.context), journal or {})

    def test_us1_unattended_routes_must_name_their_instance(self):
        for selected_by in ("single registration", "protected default"):
            if selected_by == "protected default":
                pf_instance.set_default_instance(self.layout.root, self.context.instance_id)
            for command in ("backup", "release-check"):
                with self.subTest(selected_by=selected_by, command=command):
                    code, out, err = self.run_main([command], terminal=False)
                    self.assertEqual(code, 1, err)
                    self.assertIn(f"ERROR: instance-required-unattended: '{command}' is running without a terminal and "
                                  f"selected instance staging by {selected_by}. Unattended commands must name the "
                                  f"instance: pf --instance <slug|uuid> {command}. Nothing was changed.", err)
                    self.assert_nothing_started()

    def test_us2_a_terminal_passes_the_gate_and_reaches_the_ownership_preflight(self):
        fragment = pfx.owned_topology(self.context)
        fragment["volumes"] = [pfx.volume(PROJECT + "_postgres_data", {})]
        self.state(fragment)
        code, out, err = self.run_main(["backup"], terminal=True)
        self.assertEqual(code, 1, err)
        self.assertIn("resource-not-owned: backup refused before any change", err)
        for gate in GATE_CODES:
            self.assertNotIn(gate, err)

    def test_us3_read_only_routes_behave_as_before_without_a_terminal(self):
        state = self.fake.state()
        state["compose"]["psql"] = {"SELECT to_regclass('public.alembic_version') IS NOT NULL;": "f"}
        self.fake.write_state(state)
        for command in ("status", "doctor", "backups", "recoveries", "ps"):
            with self.subTest(command=command):
                code, out, err = self.run_main([command], terminal=False)
                self.assertEqual(code, 0, out + err)
                for gate in GATE_CODES:
                    self.assertNotIn(gate, err)
        self.assertEqual(self.operation_dirs(), [])

    def test_us4_release_apply_is_refused_by_policy_with_or_without_a_terminal(self):
        self.set_config(auto_update=True)
        for terminal in (True, False):
            with self.subTest(terminal=terminal), mock.patch.object(pf.Controller, "github") as github:
                code, out, err = self.run_main(["--instance", "staging", "release-check", "--apply"], terminal=terminal)
                self.assertEqual(code, 20, err)
                self.assertIn("ERROR: auto-apply-not-permitted: release apply needs a protected policy that permits "
                              "it; approved policy revision 1 of instance staging does not (automatic apply is off in "
                              "this checkpoint). The editable auto_update setting is a proposal only.", err)
                github.assert_not_called()
                self.assert_nothing_started()
        controller = self.controller()
        original = self.layout.policy_path.read_bytes()
        try:
            self.layout.policy_path.write_bytes(pfx.policy_document(revision=1).replace(b"1", b"1 "))
            with self.assertRaisesRegex(pf.Failure, "Approved policy bytes do not match"):
                controller.policy_permits("auto-apply")
        finally:
            self.layout.policy_path.write_bytes(original)
        self.assertFalse(controller.policy_permits("auto-apply"))

    def test_us5_a_check_only_release_check_keeps_its_behaviour_at_a_terminal(self):
        def github(controller, path, missing=False):
            if path == "releases/latest":
                return {"id": 9, "tag_name": "v9.9.9", "published_at": "2026-10-06T00:00:00Z", "draft": False,
                        "prerelease": False}
            self.assertEqual(path, "commits/v9.9.9")
            return {"sha": pfx.NEW}

        with mock.patch.object(pf.Controller, "github", github):
            code, out, err = self.run_main(["--instance", "staging", "release-check"], terminal=True)
        self.assertEqual(code, 0, err)
        self.assertIn("Selected release: v9.9.9 -> " + pfx.NEW, out)
        self.assertIn("Check-only. No code or database changes were made.", out)

    def test_us6_confirmation_routes_need_a_terminal_and_stop_before_the_lock(self):
        cases = (["update", "--latest"], ["deploy"], ["reset-db"], ["rollback", BACKUP_ID], ["resume"],
                 ["abort-deploy"], ["purge", "--keep-backups"], ["restore-instance", RECOVERY_ID])
        for arguments in cases:
            with self.subTest(arguments=arguments):
                journal = self.write_journal() if arguments[0] == "resume" else None
                with mock.patch.object(pf.Controller, "github") as github, \
                        mock.patch.object(pf.Controller, "fail_closed") as fail_closed:
                    code, out, err = self.run_main(["--instance", "staging", *arguments], terminal=False)
                self.assertEqual(code, 1, err)
                self.assertIn(f"ERROR: terminal-required: '{arguments[0]}' asks for a typed confirmation and cannot run "
                              "without a terminal (scheduled task, script, or ssh without -t). Run it interactively: "
                              f"sudo pf --instance staging {arguments[0]}. Nothing was changed.", err)
                github.assert_not_called()
                fail_closed.assert_not_called()
                self.assert_nothing_started(journal)
                if journal is not None:
                    pfx.clear_operations(self.context)

    def test_us7_policy_routes_need_a_protected_grant_without_a_terminal(self):
        # PF-A2.3: `permissions` left the policy-grant list (OD-A23-11): check/plan are read-only, apply is terminal.
        code, out, err = self.run_main(["--instance", "staging", "permissions", "apply"], terminal=False)
        self.assertEqual(code, 1, err)
        self.assertIn("ERROR: terminal-required: 'permissions apply' asks for a typed confirmation and cannot run "
                      "without a terminal (scheduled task, script, or ssh without -t). Run it interactively: sudo pf "
                      "--instance staging permissions apply. Nothing was changed.", err)
        self.assert_nothing_started()
        for verb in ("check", "plan"):
            code, out, err = self.run_main(["permissions", verb], terminal=False)
            for gate in GATE_CODES:
                self.assertNotIn(gate, err)
            self.assertIn("Permission " + verb + " for instance staging", out)
            self.assert_nothing_started()
        for command in ("backup", "release-check"):
            with self.subTest(command=command), \
                    mock.patch.object(pf.Controller, "policy_permits", autospec=True,
                                      side_effect=pf.Controller.policy_permits) as permits:
                code, out, err = self.run_main(["--instance", "staging", command], terminal=False)
                self.assertEqual(code, 20, err)
                self.assertIn(f"ERROR: policy-grant-required: unattended '{command}' needs a protected policy that "
                              "permits this class of operation; approved policy revision 1 of instance staging permits "
                              "no unattended operation in this checkpoint (grants arrive with PF-A4.3).", err)
                self.assertEqual([call.args[1] for call in permits.call_args_list], [command])
                self.assert_nothing_started()
                self.assertFalse((self.context.state_dir / "observed-tags.json").exists())

    def test_us8_absent_or_closed_stdin_is_unattended(self):
        closed = io.StringIO()
        closed.close()
        for stream in (None, closed):
            with self.subTest(stream=type(stream).__name__), mock.patch.object(sys, "stdin", stream):
                self.assertTrue(pf.unattended())
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = pf.main(["backup"], installation_root=self.layout.root,
                                   running_release=self.layout.release_dir, trusted_launch=True)
                self.assertEqual(code, 1)
                self.assertIn("instance-required-unattended", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
        with mock.patch.object(sys, "stdin", None), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(pf.Failure, "requires an interactive terminal; no --yes bypass exists"):
                pf.confirm("RESET x", "warning")
        self.assertEqual(self.fake.calls(), [])


# ============================================================================ DT-2b: install verbs need a terminal


@ROOT_REQUIRED
class InstallTerminalGate(Base):
    """DT-2b (PF-A2.1): every `pf install` verb except `status` refuses without a terminal and writes nothing."""

    def test_dt2b_every_install_verb_but_status_needs_a_terminal(self):
        before = pfx.snapshot_tree(self.base)
        for verb, extra in (("register", ["--slug", "x"]), ("migrate-legacy", []), ("control", ["--source", "/x"]),
                            ("resume", []), ("resume", ["--abandon"])):
            with self.subTest(verb=verb, extra=extra):
                code, out, err = self.run_main(["install", verb, *extra], terminal=False)
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: terminal-required: 'install {verb}' asks for a typed confirmation and cannot run "
                              f"without a terminal. Run it interactively: sudo {self.layout.root}/bootstrap/pf install "
                              f"{verb} …. Nothing was changed.", err)
                self.assertEqual(pfx.snapshot_tree(self.base), before)
                self.assertEqual(self.fake.calls(), [])
        code, out, err = self.run_main(["install", "status"], terminal=False, trusted_launch=False)
        self.assertEqual((code, out.strip()), (0, "No install operations."), err)
        self.assertEqual(pfx.snapshot_tree(self.base), before)


# ============================================================================ SW: scheduler wrappers


@ROOT_REQUIRED
class SchedulerWrappers(Base):
    """E5/E6: thin wrappers with an explicit instance that exec only the sibling launcher."""

    def place(self, directory_name, wrapper, *, launcher="pf"):
        directory = self.base / "wrappers" / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o755)
        target = directory / wrapper
        shutil.copy2(PACKAGE / wrapper, target)
        os.chmod(target, 0o755)
        record = directory / (launcher + ".record")
        stub = directory / launcher
        stub.write_text("#!/bin/sh\n{ printf '%s\\n' \"$@\"; echo ---; env; } > " + str(record) + "\nexit 0\n")
        os.chmod(stub, 0o755)
        return target, record

    def call(self, wrapper, arguments, env=None):
        environment = {"PATH": "/usr/bin:/bin"} if env is None else env
        return subprocess.run([str(wrapper), *arguments], env=environment, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=120)

    @staticmethod
    def recorded(record):
        argv, _, env = record.read_text().partition("---\n")
        return argv.splitlines(), dict(line.split("=", 1) for line in env.splitlines() if "=" in line)

    def test_sw1_backup_wrapper_refuses_a_missing_or_unsafe_instance(self):
        wrapper, record = self.place("bootstrap", "backup.sh")
        for arguments in ([], ["--instance"], ["--instance", ""], ["--instance", "-x"], ["--instance", "a b"],
                          ["--instance", "../x"], ["--instance", "staging", "extra"], ["staging"]):
            with self.subTest(arguments=arguments):
                result = self.call(wrapper, arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("Usage: backup.sh --instance <slug|uuid>. A scheduled task must name its instance; "
                              "nothing was run.", result.stderr)
                self.assertFalse(record.exists())

    def test_sw2_wrappers_exec_the_sibling_launcher_with_a_fixed_argument_vector(self):
        backup, record = self.place("bootstrap", "backup.sh")
        result = self.call(backup, ["--instance", "staging"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(record)[0], ["--instance", "staging", "backup"])
        check, record = self.place("bootstrap", "release-check.sh")
        record.unlink()
        result = self.call(check, ["--instance", "staging", "--apply", "--channel", "prerelease"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(record)[0],
                         ["--instance", "staging", "release-check", "--channel", "prerelease", "--apply"])
        record.unlink()
        for arguments in (["--instance", "staging", "--apply", "--apply"], ["--instance", "staging", "--force"],
                          ["--instance", "staging", "--channel", "beta"], ["--instance", "staging", "--channel"],
                          ["--channel", "stable", "--instance", "staging"]):
            with self.subTest(arguments=arguments):
                result = self.call(check, arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("Usage: release-check.sh --instance <slug|uuid>", result.stderr)
                self.assertFalse(record.exists())

    def test_sw3_wrappers_run_only_from_bootstrap_or_control(self):
        for wrapper in ("backup.sh", "release-check.sh"):
            with self.subTest(wrapper=wrapper):
                path, record = self.place("elsewhere", wrapper)
                result = self.call(path, ["--instance", "staging"])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("This wrapper runs only from an installed bootstrap/ or legacy control/ directory.",
                              result.stderr)
                self.assertFalse(record.exists())

    def test_sw4_legacy_control_wrapper_reports_the_command_word(self):
        home = self.base / "legacyhome"
        control = home / "control"
        control.mkdir(parents=True)
        shutil.copy2(REPO_PACKAGE / "pf.sh", control / "pf.sh")
        os.chmod(control / "pf.sh", 0o700)
        shutil.copy2(PACKAGE / "backup.sh", control / "backup.sh")
        os.chmod(control / "backup.sh", 0o700)
        (home / "config").mkdir()
        before = pfx.snapshot_tree(home)
        result = self.call(control / "backup.sh", ["--instance", "staging"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("UNREGISTERED legacy installation (v2.5 layout) at " + str(home), result.stdout)
        self.assertIn("ERROR: 'backup' is refused on an unregistered installation; read-only diagnostics only.",
                      result.stderr)
        self.assertNotIn("'--instance'", result.stderr)
        self.assertEqual(pfx.snapshot_tree(home), before)

    def test_sw5_the_inherited_environment_never_reaches_the_launcher(self):
        wrapper, record = self.place("bootstrap", "backup.sh")
        hostile_bin = self.base / "hostile-bin"
        hostile_bin.mkdir()
        result = self.call(wrapper, ["--instance", "staging"], env={
            "PATH": f"{hostile_bin}:/usr/bin:/bin", "PF_HOME": "/elsewhere", "PYTHONPATH": "/elsewhere/python",
            "DOCKER_HOST": "tcp://127.0.0.1:1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        argv, env = self.recorded(record)
        self.assertEqual(env["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        for name in ("PF_HOME", "PYTHONPATH", "DOCKER_HOST"):
            self.assertNotIn(name, env)
        self.assertEqual(argv, ["--instance", "staging", "backup"])

    def test_sw6_installed_wrappers_reach_the_policy_gates(self):
        self.set_config(auto_update=True)
        # PF-A2.1 (OD-A14-05): the wrappers placed in bootstrap/ by initialize_installation_root(wrappers=...).
        backup = self.layout.root / "bootstrap" / "backup.sh"
        check = self.layout.root / "bootstrap" / "release-check.sh"
        for path in (backup, check):
            self.assertEqual(path.read_bytes(), (PACKAGE / path.name).read_bytes())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)
        transcripts = {}
        for label, path, arguments, code in (
                ("release-check-apply", check, ["--instance", "staging", "--apply"], "auto-apply-not-permitted"),
                ("release-check", check, ["--instance", "staging"], "policy-grant-required"),
                ("backup", backup, ["--instance", "staging"], "policy-grant-required")):
            with self.subTest(label=label):
                before = self.identities()
                state_before = self.fake.state_bytes()
                result = self.call(path, arguments, env={"PATH": "/usr/bin:/bin", "TERM": "dumb"})
                self.assertEqual(result.returncode, 20, result.stdout + result.stderr)
                self.assertIn("ERROR: " + code + ":", result.stderr)
                self.assertEqual(self.fake.calls(), [])
                self.assertEqual(self.operation_dirs(), [])
                self.assertEqual(self.fake.state_bytes(), state_before)
                transcripts[label] = {"argv": [str(path)] + arguments, "exit": result.returncode,
                                      "stdout": result.stdout, "stderr": result.stderr,
                                      "resource_identities_before": before,
                                      "resource_identities_after": self.identities(),
                                      "fake_daemon_calls": self.fake.calls()}
        record_evidence("SW-6", transcripts)


# ============================================================================ EH: error handler


@ROOT_REQUIRED
class ErrorHandler(Base):
    """E9/E10: fail_closed stops this instance's own one-off jobs from the exact inventory, then the app."""

    def oneoff(self, cid, name, *, context=None, status="running"):
        return pfx.container(cid, name, pfx.compose_run_labels(context or self.context, "backend"), status=status)

    def setUp(self):
        super().setUp()
        self.other, _ = self.instance("other", "partflow-other")
        fragment = pfx.owned_topology(self.context)
        fragment["containers"] += [
            self.oneoff("b" * 64, PROJECT + "-backend-run-0a1b2c3d4e5f"),
            self.oneoff("1" * 64, PROJECT + "-backend-run-6a7b8c9d0e1f", status="exited"),
            pfx.container("e" * 64, "foreign-job", {"partflow.admin.project": PROJECT}),
            self.oneoff("f" * 64, "partflow-other-backend-run-1", context=self.other),
        ]
        self.state(fragment)

    def stops(self):
        return [argv for argv in self.fake.argvs() if argv[:1] == ["stop"]]

    @contextlib.contextmanager
    def failed_operation(self):
        """A locked `resume` whose body failed after its confirmation: the operation's own lock, re-entered journal
        and preflight are in place (PF-A3.2: a refused re-entry never fails closed, section 3.9)."""
        self.write_journal()
        controller = self.controller()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), controller.lock("resume"):
            controller._reentered = True
            controller.require_topology_owned("resume")
            self.fake.clear_calls()
            yield controller, output

    def test_eh1_only_owned_oneoffs_are_stopped_then_the_application(self):
        before = self.identities()
        with self.failed_operation() as (controller, output):
            controller.fail_closed()
        self.assertEqual(self.stops(), [["stop", "--time", "30", "1" * 64], ["stop", "--time", "30", "b" * 64]])
        self.assertEqual([verb for verb in self.verbs() if verb[:1] == ["stop"]], [["stop", "frontend", "backend"]])
        calls = self.fake.argvs()
        self.assertLess(calls.index(["stop", "--time", "30", "b" * 64]),
                        max(index for index, argv in enumerate(calls) if argv[:1] == ["compose"]))
        containers = {item["id"]: item for item in self.fake.state()["containers"]}
        self.assertEqual(containers["e" * 64]["status"], "running")
        self.assertEqual(containers["f" * 64]["status"], "running")
        self.assertEqual(containers["b" * 64]["status"], "exited")
        self.assertIn("Operation incomplete. Application services are intentionally stopped. Inspect "
                      "'pf --instance staging status'. Automation and Compose writes remain blocked.", output.getvalue())
        after = self.identities()
        changed = [item for item in after["containers"] if item not in before["containers"]]
        self.assertEqual([item[0] for item in changed], ["b" * 64])
        record_evidence("EH-1", {"resource_identities_before": before, "resource_identities_after": after,
                                 "fake_daemon_calls": calls, "output": output.getvalue()})

    def test_eh2_an_inventory_failure_still_stops_the_application(self):
        churn = [pfx.container(str(index) * 64, "cron-" + str(index), {}, status="exited") for index in (7, 8, 9)]
        state = self.fake.state()
        state["containers"] += churn
        self.fake.write_state(state)
        with self.failed_operation() as (controller, output):
            state = self.fake.state()
            state["hooks"] = [{"after_argv_prefix": ["ps", "-a"], "nth": number,
                               "mutate": [{"op": "remove", "list": "containers", "match": {"id": item["id"]}}]}
                              for number, item in enumerate(churn, start=1)]
            self.fake.write_state(state)
            controller.fail_closed()
        self.assertIn("WARNING: Could not list this instance's one-off containers: inventory-unstable", output.getvalue())
        self.assertEqual(self.stops(), [])
        self.assertIn(["stop", "frontend", "backend"], self.verbs())

    def test_eh3_a_cached_daemon_refusal_starts_nothing(self):
        with self.failed_operation() as (controller, output):
            state = self.fake.state()
            state["info"] = pfx.daemon_info("OTHER-ENGINE-9999")
            self.fake.write_state(state)
            with self.assertRaises(pf.DaemonFailure):
                controller.verify_daemon(refresh=True)
            self.fake.clear_calls()
            controller.fail_closed()
            self.assertEqual(self.fake.calls(), [])
        warnings = [line for line in output.getvalue().splitlines() if line.startswith("WARNING:")]
        self.assertEqual(len(warnings), 1, output.getvalue())
        self.assertIn("daemon-drift", warnings[0])

    def test_eh4_copy_names_the_instance_and_no_legacy_label_selects(self):
        with self.failed_operation() as (controller, output):
            controller.fail_closed()
        self.assertIn("pf --instance staging status", output.getvalue())
        self.assertNotIn("pf.sh status", output.getvalue())
        self.assertNotIn("partflow.admin.project", inspect.getsource(pf.Controller.fail_closed))
        self.assertFalse([argv for argv in self.fake.argvs() if "--filter" in argv and argv[:1] == ["ps"]])

    def test_eh5_a_vanished_oneoff_is_skipped_and_the_original_failure_is_kept(self):
        self.write_journal()
        self.oneoff_exited("b" * 64)
        state = self.fake.state()
        state["compose"]["stop_fail"] = {"1" * 64: "Error response from daemon: No such container: " + "1" * 64}
        state["compose"]["fail_verbs"] = ["exec"]  # the confirmed resume fails on the migration's database check
        self.fake.write_state(state)
        self.fake.clear_calls()
        with mock.patch.object(pf, "confirm"):
            code, out, err = self.run_main(["--instance", "staging", "resume"], interactive=True)
        self.assertEqual(code, 1, err)
        self.assertIn("fake compose exec failed", err)
        self.assertIn("WARNING: Could not stop one-off container 111111111111: Error response from daemon: No such "
                      "container", out)
        self.assertEqual(self.stops(), [["stop", "--time", "30", "1" * 64], ["stop", "--time", "30", "b" * 64]])
        self.assertIn(["stop", "frontend", "backend"], self.verbs())

    def test_eh7_a_real_signal_through_the_installed_launcher_runs_fail_closed_before_the_lock_is_free(self):
        """E10 (PF-A1.4 audit A14-AUD-03): each catchable signal, sent for real to the installed launcher during
        an `always` route (`resume`) with an existing journal, unwinds into fail_closed: the owned one-offs are
        stopped, then `compose stop frontend backend`, all while the instance lock is still held."""
        marker = self.base / "blocked-child.pid"
        record = self.base / "lock-probe.txt"
        for signum in (signal.SIGHUP, signal.SIGQUIT, signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum.name):
                pfx.clear_operations(self.context)
                self.write_journal()
                self.oneoff_exited("b" * 64)
                for path in (marker, record):
                    if path.exists():
                        path.unlink()
                state = self.fake.state()
                # resume -> forward migration -> `compose exec -T db ...` is the child the signal interrupts.
                state["block"] = {"argv_contains": ["compose", "exec", "-T", "db"], "seconds": 300,
                                  "marker": str(marker)}
                state["lock_probe"] = {"argv_contains": ["stop"], "lock": str(self.context.lock_path),
                                       "record": str(record)}
                self.fake.write_state(state)
                self.fake.clear_calls()
                with open(str(self.base / "cli-out.txt"), "wb") as out, \
                        open(str(self.base / "cli-err.txt"), "wb") as err, \
                        pfx.typed_terminal(["RESUME " + self.journal_plan["operation_id"][-8:]]) as terminal:
                    process = subprocess.Popen(
                        [str(self.layout.launcher), "--instance", "staging", "resume"],
                        env={"PATH": "/usr/bin:/bin", "TERM": "dumb"}, cwd=str(self.layout.root.parent),
                        stdin=terminal, stdout=out, stderr=err)
                    try:
                        deadline = time.monotonic() + 60
                        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertTrue(marker.exists(), (self.base / "cli-err.txt").read_text())
                        time.sleep(0.3)  # the blocked child is running; the controller is in the runner
                        process.send_signal(signum)
                        returncode = process.wait(timeout=120)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                stdout = (self.base / "cli-out.txt").read_text()
                stderr = (self.base / "cli-err.txt").read_text()
                self.assertEqual(returncode, 1, stderr)
                self.assertIn(f"Interrupted by signal {int(signum)}", stderr)
                calls = self.fake.argvs()
                blocked = next(index for index, argv in enumerate(calls)
                               if argv[:1] == ["compose"] and "exec" in argv and "db" in argv)
                after = calls[blocked + 1:]
                self.assertEqual([argv for argv in after if argv[:1] == ["stop"]],
                                 [["stop", "--time", "30", "1" * 64], ["stop", "--time", "30", "b" * 64]])
                compose_stop = [index for index, argv in enumerate(after)
                                if argv[:1] == ["compose"] and argv[-3:] == ["stop", "frontend", "backend"]]
                self.assertEqual(len(compose_stop), 1, after)
                self.assertLess(after.index(["stop", "--time", "30", "b" * 64]), compose_stop[0])
                # Every stop ran while this process still held the instance lock.
                self.assertEqual(record.read_text().splitlines(),
                                 ["--time 30 " + "1" * 64 + " held", "--time 30 " + "b" * 64 + " held",
                                  "stop frontend backend held"])
                self.assertIn("Operation incomplete. Application services are intentionally stopped. Inspect "
                              "'pf --instance staging status'. Automation and Compose writes remain blocked.", stdout)
                self.assertEqual(pfx.open_operations(self.context), [("deploy", "initializing")])  # kept for status
                self.assert_lock_free()

    def test_eh6_container_identity_shape_is_unchanged_and_frozen_plans_still_compare(self):
        container = {"id": "c" * 64, "name": "/x", "labels": pfx.compose_run_labels(self.context, "backend"),
                     "created": "2026-10-06T00:00:00Z", "image": "sha256:i"}
        self.assertEqual(set(pf_docker._container_identity(container)),
                         {"id", "name", "service", "oneoff", "created", "image"})
        controller = self.controller()
        inventory = controller.docker_inventory()
        # A PF-A1.3 frozen plan: candidate identities in the PF-A1.3 shape, written as literal bytes.
        plan = pf_docker.plan_deletion(inventory, kind="purge", operation_id="op-a13", daemon={
            "endpoint": self.context.daemon.endpoint, "engine_id": pfx.ENGINE_ID}, covered_image_refs=set())
        data = pf_docker.plan_bytes(plan)
        loaded = json.loads(data)
        for item in loaded["candidates"]:
            if item["kind"] == "container":
                self.assertEqual(set(item["identity"]), {"id", "name", "service", "oneoff", "created", "image"})
        fresh = controller.docker_inventory()
        for item in loaded["candidates"]:
            with self.subTest(item=item["key"]):
                self.assertEqual(pf_docker.compare_identity(item, *fresh.index(item["kind"])), "identical")
        self.assertEqual(pf_docker.owned_oneoffs(fresh), ("1" * 64, "b" * 64))


# ============================================================================ CI: cross-instance authority


@ROOT_REQUIRED
class CrossInstance(Base):
    """ARCH section 4, DA-08: --project never selects a path; restore authority is the selected instance's own."""

    def bundle(self, folder, *, project=None, root=None, state_files=()):
        """PF-A3.1: a complete legacy format 2 purge bundle (the strict reader accepts it before these checks)."""
        tree = self.base / "bundle-tree"
        if not tree.exists():
            pfx.source_fixture(tree)
        return pfx.legacy_purge_bundle(folder, project=project or self.context.compose_project,
                                       root=root or self.context.paths.workspace, tree=tree,
                                       extra={"state_files": list(state_files)})

    def own(self, recovery_id=RECOVERY_ID, **kwargs):
        return self.bundle(self.context.paths.recovery / self.context.compose_project / recovery_id, **kwargs)

    def test_ci1_project_must_name_the_selected_instance(self):
        self.own()
        for arguments in (["purge", "--project", "other"], ["recoveries", "--project", "other"],
                          ["restore-instance", RECOVERY_ID, "--project", "other"]):
            with self.subTest(arguments=arguments):
                code, out, err = self.run_main(["--instance", "staging", *arguments], interactive=True)
                self.assertEqual(code, 1, err)
                self.assertIn("ERROR: selection-conflict: --project other does not match the selected instance "
                              "staging (project partflow-staging). Use --instance alone. Nothing was changed.", err)
                self.assertEqual(self.fake.calls(), [])
                self.assertEqual(self.operation_dirs(), [])
                self.assert_lock_free()

    def test_ci2_project_is_a_name_never_a_path(self):
        self.own()
        listed = []
        original = Path.iterdir

        def iterdir(path):
            if self.context.paths.recovery in (path, *path.parents):
                listed.append(path)
            return original(path)

        with mock.patch.object(Path, "iterdir", iterdir):
            for value in ("../repo", "UPPER", "a/b"):
                with self.subTest(project=value):
                    with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                        self.run_main(["recoveries", "--project", value])
                    self.assertEqual(caught.exception.code, 2)
            self.assertEqual(listed, [])
            code, out, err = self.run_main(["--instance", "staging", "recoveries"])
        self.assertEqual(code, 0, err)
        self.assertIn(RECOVERY_ID, out)
        self.assertEqual(set(listed), {self.context.paths.recovery / self.context.compose_project})

    def test_ci3_forged_bundles_elsewhere_are_never_listed_or_restored(self):
        self.own()
        other_id = "purge-20261006T000001Z-" + pfx.OLD[:12] + "-bbbbbb"
        workspace_id = "purge-20261006T000002Z-" + pfx.OLD[:12] + "-cccccc"
        forged = self.bundle(self.context.paths.recovery / "partflow-other" / other_id)
        planted = self.bundle(self.context.paths.workspace / workspace_id)
        code, out, err = self.run_main(["--instance", "staging", "recoveries"])
        self.assertEqual(code, 0, err)
        self.assertIn(RECOVERY_ID, out)
        self.assertNotIn(other_id, out)
        self.assertNotIn(workspace_id, out)
        for recovery_id in (other_id, workspace_id):
            with self.subTest(recovery_id=recovery_id):
                code, out, err = self.run_main(["--instance", "staging", "restore-instance", recovery_id],
                                               interactive=True)
                self.assertEqual(code, 1, err)
                self.assertIn("Specify one exact purge recovery ID.", err)
        controller = self.controller()
        for folder in (forged, planted):
            with self.subTest(folder=str(folder)):
                with self.assertRaisesRegex(pf.Failure, "^recovery-outside-instance: " + re.escape(str(folder))):
                    controller.verify_recovery({"_folder": str(folder), "id": folder.name})
        self.assertFalse(self.context.journal_path.exists())

    def test_ci4_a_state_file_outside_the_allowlist_is_refused_before_confirmation(self):
        self.own(state_files=["deployed.json", "../../escape.json"])
        (self.context.state_dir / "deployed.json").unlink()
        env_before = (self.paths["configuration"] / ".env").read_bytes()
        with mock.patch.object(pf, "confirm", side_effect=AssertionError("no confirmation")) as confirm:
            code, out, err = self.run_main(["--instance", "staging", "restore-instance", RECOVERY_ID],
                                           interactive=True)
        self.assertEqual(code, 1, err)
        self.assertIn(f"ERROR: recovery-state-file-refused: bundle {RECOVERY_ID} lists state file "
                      "'../../escape.json'; only deployed.json, last-reset.json, observed-tags.json can be restored "
                      "into protected state. Nothing was changed.", err)
        confirm.assert_not_called()
        self.assertFalse(self.context.journal_path.exists())
        self.assertEqual((self.paths["configuration"] / ".env").read_bytes(), env_before)
        self.assertEqual(self.fake.calls(), [])

    def test_ci4_state_files_that_are_not_a_list_are_refused_before_confirmation(self):
        """PF-A1.4 audit A14-AUD-01: the checked value is the consumed value. A string was wrapped for the
        allowlist check and then iterated per character by the restore; a dict was checked by its keys."""
        (self.context.state_dir / "deployed.json").unlink()
        env_before = (self.paths["configuration"] / ".env").read_bytes()
        for label, value in (("str", "observed-tags.json"), ("dict", {"deployed.json": True})):
            with self.subTest(state_files=label):
                folder = self.own()
                try:
                    manifest = pf.load_json(folder / "manifest.json")
                    manifest["state_files"] = value
                    pf.write_json(folder / "manifest.json", manifest)
                    (folder / "manifest.sha256").write_text(pf.digest(folder / "manifest.json") + "\n")
                    (folder / "state").mkdir(exist_ok=True)
                    (folder / "state" / "o").write_text("planted\n")
                    with self.assertRaisesRegex(pf.Failure, "^recovery-state-file-refused: bundle " + RECOVERY_ID
                                                + " lists state files as " + label + ", not a list of file names; "):
                        self.controller().verify_recovery({"_folder": str(folder), "id": folder.name})
                    with mock.patch.object(pf, "confirm", side_effect=AssertionError("no confirmation")) as confirm:
                        code, out, err = self.run_main(["--instance", "staging", "restore-instance", RECOVERY_ID],
                                                       interactive=True)
                    self.assertEqual(code, 1, err)
                    self.assertIn(f"ERROR: recovery-state-file-refused: bundle {RECOVERY_ID} lists state files as "
                                  f"{label}, not a list of file names; only deployed.json, last-reset.json, "
                                  "observed-tags.json can be restored into protected state. Nothing was changed.", err)
                    confirm.assert_not_called()
                    self.assertFalse((self.context.state_dir / "o").exists())
                    self.assertFalse(self.context.journal_path.exists())
                    self.assertEqual((self.paths["configuration"] / ".env").read_bytes(), env_before)
                    self.assertEqual(self.fake.calls(), [])
                finally:
                    shutil.rmtree(str(folder))

    def test_ci3_a_linked_bundle_directory_is_neither_listed_nor_restored(self):
        """PF-A1.4 audit A14-AUD-02: listing and restore share one rule; a `purge-*` link to a bundle outside
        the instance is not listed with the outside manifest and cannot be restored."""
        outside = self.bundle(self.base / "outside" / RECOVERY_ID, project="outside-project")
        root = self.context.paths.recovery / self.context.compose_project
        root.mkdir(parents=True, exist_ok=True)
        link = root / RECOVERY_ID
        os.symlink(str(outside), str(link))
        self.assertEqual(self.controller().recoveries(), [])
        code, out, err = self.run_main(["--instance", "staging", "recoveries"])
        self.assertEqual(code, 0, err)
        self.assertNotIn(RECOVERY_ID, out)
        self.assertNotIn("outside-project", out)
        code, out, err = self.run_main(["--instance", "staging", "restore-instance", RECOVERY_ID], interactive=True)
        self.assertEqual(code, 1, err)
        self.assertIn("ERROR: No purge recovery bundles were found.", err)
        with self.assertRaisesRegex(pf.Failure, "^recovery-outside-instance: " + re.escape(str(link))):
            self.controller().verify_recovery({"_folder": str(link), "id": RECOVERY_ID})
        self.assertFalse(self.context.journal_path.exists())

    def test_ci5_a_bundle_of_another_project_or_root_is_refused_before_confirmation(self):
        # PF-A3.3 (SPEC section 6.3 -> RX-1/RX-3): a legacy bundle of another project is refused by identity
        # (restore-target-mismatch) before any confirmation; another recorded root is provenance only (a note): the
        # restore goes on with the selected instance's paths (here to the empty-target refusal of this running
        # instance) and never writes under the recorded root.
        (self.context.state_dir / "deployed.json").unlink()
        other = self.base / "other" / "repo"
        for label, kwargs, message in (
                ("project", {"project": "partflow-other"},
                 f"restore-target-mismatch: bundle {RECOVERY_ID} belongs to project partflow-other"),
                ("root", {"root": other}, "resource-target-not-empty:")):
            with self.subTest(label=label):
                folder = self.own(**kwargs)
                before = pfx.snapshot_tree(self.base / "staging")
                with mock.patch.object(pf, "confirm", side_effect=AssertionError("no confirmation")):
                    code, out, err = self.run_main(["--instance", "staging", "restore-instance", RECOVERY_ID],
                                                   interactive=True)
                self.assertEqual(code, 1, err)
                self.assertIn(message, err)
                if label == "root":
                    self.assertIn(f"note: bundle-workspace-differs: the bundle recorded workspace {other}", out + err)
                    self.assertFalse(other.exists())
                self.assertEqual(pfx.snapshot_tree(self.base / "staging"), before)
                self.assertFalse(self.context.journal_path.exists())
                shutil.rmtree(folder)


# Purge resume through the real controller, the fake daemon and the PF-A1.3 harness (CI-6).
import test_docker_scope as docker_scope  # noqa: E402


@ROOT_REQUIRED
class PurgeResumeAuthority(docker_scope.PurgeHarness):
    """CI-6: purge resume verifies the bundle with the PF-A1.4 checks before its resume confirmation."""

    def interrupted_purge(self):
        self.state(docker_scope.topology(self.context))
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
        self.assertEqual(code, 1, err)
        return self.journal()

    def test_ci6_resume_accepts_the_instance_bundle_and_refuses_one_outside(self):
        # PF-A3.2 (section 3.6): the purge resume re-reads the plan's own bundle before its confirmation. The bundle
        # folder replaced by a link to a byte-identical copy outside the instance refuses plan-input-changed and
        # changes no operation file; the restored folder resumes.
        self.interrupted_purge()
        op, plan, journal = self.operation()
        self.assertEqual(journal["phase"], "deleting")
        bundle_id = next(item.split(":", 1)[1] for effect in plan["effects"] if effect["target"] == "purge-bundle"
                         for item in effect["preconditions"] if item.startswith("bundle:"))
        recovery = self.context.paths.recovery / docker_scope.PROJECT / bundle_id
        self.assertTrue(recovery.is_dir())
        outside = self.base / "outside" / bundle_id
        shutil.copytree(str(recovery), str(outside), symlinks=True)
        held = self.base / "held" / bundle_id
        held.parent.mkdir()
        os.rename(str(recovery), str(held))
        os.symlink(str(outside), str(recovery))
        before = pfx.operations_bytes(self.context)
        code, out, err, confirmations = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: plan-input-changed: recovery bundle {bundle_id} of operation {op} no longer reads or "
                      "verifies (recovery-outside-instance); deletion stays blocked", err)
        self.assertEqual(confirmations, [])
        self.assertEqual(pfx.operations_bytes(self.context), before)
        os.unlink(str(recovery))
        os.rename(str(held), str(recovery))
        code, out, err, confirmations = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(confirmations, ["RESUME PURGE partflow " + bundle_id])
        self.assertEqual(self.journal()["phase"], "completed")


# ============================================================================ SS: static scan


class StaticScan(unittest.TestCase):
    """PF-A1 section 7 paragraph 4: static guards over every release module and shell entry point."""

    def modules(self):
        return {name: parse_module(name) for name in RELEASE_MODULES}

    def test_ss1_process_launch_has_one_boundary(self):
        popen, execv, runner_runs = [], [], []
        for name, (source, tree) in self.modules().items():
            with self.subTest(module=name):
                imports = [alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                           for alias in node.names] + [node.module for node in ast.walk(tree)
                                                       if isinstance(node, ast.ImportFrom) and node.module]
                self.assertEqual("subprocess" in imports, name == "pf_runner.py")
                self.assertNotIn("pty", imports)
                self.assertNotIn("asyncio", imports)
                for call, where in qualified_calls(tree):
                    target = dotted(call.func) or ""
                    self.assertFalse(re.fullmatch(r"os\.(system|popen|spawn\w*|fork\w*|posix_spawn\w*)", target),
                                     (name, target))
                    self.assertFalse(target.startswith("asyncio.create_subprocess"), target)
                    if target == "subprocess.Popen":
                        popen.append((name, where))
                    if re.fullmatch(r"os\.exec\w*", target):
                        execv.append((name, where, target))
                    if target == "self.runner.run":
                        runner_runs.append((name, where))
                    for keyword in call.keywords:
                        if keyword.arg == "shell":
                            self.assertFalse(isinstance(keyword.value, ast.Constant) and keyword.value.value,
                                             (name, where))
        self.assertEqual(popen, [("pf_runner.py", "ProcessRunner.run")])
        self.assertEqual(execv, [("pf_bootstrap.py", "main", "os.execv")])
        # PF-A2.1: the installer's smoke and end-to-end checks are the only other self.runner.run sites, and its two
        # read-only preflight probes (docker info, interpreter version) call the same runner boundary.
        self.assertEqual(sorted(runner_runs), [("pf-admin.py", "Controller.command"), ("pf_install.py", "_Run._apply_smoke"),
                                               ("pf_install.py", "_Run.verify_problems")])
        _, tree = parse_module("pf_install.py")
        self.assertEqual(sorted(where for call, where in qualified_calls(tree) if dotted(call.func) == "runner.run"),
                         ["_daemon_check", "_preflight_init"])
        self.assertEqual((PACKAGE / "pf-admin.py").read_text().count("self.runner.run("), 1)

    def test_ss2_context_construction_sites(self):
        _, tree = parse_module("pf-admin.py")
        sites = {}
        for call, where in qualified_calls(tree):
            target = dotted(call.func)
            if target in ("Controller", "pf_instance.load_registry", "pf_instance.resolve_instance",
                          "pf_instance.validate_context", "pf_instance.load_policy"):
                sites.setdefault(target, set()).add(where)
        self.assertEqual(sites, {
            "Controller": {"main.select"},
            # PF-A2.2: the pre-registration wizard reloads the registry under the registry lock.
            "pf_instance.load_registry": {"main", "config_admin_unregistered.conflict_checks",
                                          # PF-A3.2 section 3.7a: the registry's capacity reading for a switch.
                                          "Controller.workspace_preflight",
                                          # PF-A3.3 (read-only registry reads): the generated topology name against
                                          # registered projects (section 2.2), the other instances named in the
                                          # daemon-wide postgres:16 tag summary (section 3.5) and the project claim
                                          # of a purged record (OD-A33-08).
                                          "Controller.new_topology", "Controller.other_instances_on_daemon",
                                          "Controller.require_project_claim"},
            "pf_instance.resolve_instance": {"main.select"},
            # PF-A3.2 section 3.7: the re-validation after W3 (the bound workspace is the new generation).
            "pf_instance.validate_context": {"main.select", "Controller.ensure_validation",
                                             "Controller.workspace_validation"},
            "pf_instance.load_policy": {"Controller.policy_permits"},
        })

    def test_ss3_write_sites_are_allowlisted_and_unreachable_from_read_only_routes(self):
        for name, (_, tree) in self.modules().items():
            with self.subTest(module=name):
                self.assertEqual(write_sites(tree), WRITE_SITE_ALLOWLIST[name])
        _, tree = parse_module("pf-admin.py")
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        controller = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Controller")
        methods = {node.name: node for node in controller.body if isinstance(node, ast.FunctionDef)}

        def node_of(name):
            return methods[name.split(".", 1)[1]] if name.startswith("Controller.") else functions[name]

        def callees(node):
            found = set()
            for child in ast.walk(node):
                # self.<method>( calls and property reads (self.config, self.runner) resolve to Controller methods.
                if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == "self" \
                        and child.attr in methods:
                    found.add("Controller." + child.attr)
                elif isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in functions:
                    found.add(child.func.id)
            return found

        roots = {"Controller." + name for name in methods
                 if name in READ_ONLY_ROOTS or name.startswith(READ_ONLY_ROOT_PREFIXES)} | set(READ_ONLY_MODULE_ROOTS)
        self.assertTrue({"Controller.compose_ps", "Controller.compose_logs", "Controller.policy_permits",
                         "Controller.status", "Controller.doctor", "classify_command", "unattended"} <= roots)
        reachable, todo = set(), sorted(roots)
        while todo:
            name = todo.pop()
            if name not in reachable:
                reachable.add(name)
                todo += sorted(callees(node_of(name)) - reachable)
        found = set()
        for name in reachable:
            for child in ast.walk(node_of(name)):
                if isinstance(child, ast.Call) and tracked_write(child):
                    found.add((tracked_write(child), name))
        self.assertEqual(found, set(READ_ONLY_REACHABLE_EXEMPT))
        self.assertNotIn("Controller.lock", reachable)
        self.assertNotIn("Controller.begin_operation", reachable)

    def test_ss3d_truncation_and_write_mode_opens_are_tracked(self):
        """PF-A2.3 audit: an in-place truncation or a write-mode open of an existing file is a write site."""
        def call(source):
            return ast.parse(source).body[0].value

        self.assertEqual([tracked_write(call(text)) for text in ("os.ftruncate(fd, 0)", "os.truncate(path, 0)")],
                         ["os.ftruncate", "os.truncate"])
        self.assertEqual([install_write(call(text)) for text in (
            "os.open(path, os.O_WRONLY | os.O_NOFOLLOW)", "os.open(path, os.O_RDWR)", "os.open(path, os.O_APPEND)",
            "os.open(path, os.O_RDONLY | os.O_TRUNC)", "os.open(path, os.O_RDONLY | os.O_NOFOLLOW)")],
            ["os.open(write)", "os.open(write)", "os.open(write)", "os.open(write)", None])

    def test_ss3c_config_writer_sites_are_allowlisted_and_the_wizard_readers_write_nothing(self):
        """PF-A2.2: the wider write vocabulary (links, fchown/fchmod, exclusive creates, unlinks) in pf-admin.py is
        confined to the config writer; its read-only helpers and every read-only route reach none of it."""
        _, tree = parse_module("pf-admin.py")
        wider = {(name, where) for call, where in qualified_calls(tree) for name in [install_write(call)] if name}
        self.assertEqual(wider - write_sites(tree), {
            ("os.open(O_CREAT)", "write_editable_file"), ("os.write", "write_editable_file"),
            ("os.link", "write_editable_file"), ("os.unlink", "write_editable_file"),
            ("os.unlink", "remove_editable_leftovers"),
            # Pre-existing (PF-A1.3): the envelope render's private file inside the current operation directory.
            ("os.open(O_CREAT)", "Controller.render_compose"),
            # PF-A2.3: the write-ahead effect journal appender (O_APPEND, 0600, fsync) inside the operation directory,
            # and copy_fresh's exclusive private temporary (O_EXCL|O_NOFOLLOW relative to the held destination
            # directory) and its removal when the copy fails.
            ("os.open(O_CREAT)", "Controller._append_effects"), ("os.write", "Controller._append_effects"),
            ("os.open(O_CREAT)", "_copy_fresh_at"), ("os.unlink", "_copy_fresh_at"),
            # PF-A2.3 audit: the resume's no-follow write open of the original journal for its torn-tail ftruncate.
            ("os.open(write)", "Controller._permissions_resume"),
            # PF-A3.1: an emergency capture removes its own partial source archive before it falls back.
            ("os.unlink", "Controller._discard"),
            # PF-A3.2: the restore abandon removes the .env its own file-write effect wrote (hash-matched), and the
            # abort-deploy file-write effect removes the active-images override (section 3.6).
            ("os.unlink", "Controller.abandon_restore_instance"), ("os.unlink", "Controller.act_file"),
            # PF-A3.3: the isolated topology's own files: app.env/compose.json left by a crash before topology.json
            # was written, or of a topology that never got a Docker resource (discard_topology_files, audit finding),
            # compose.json and app.env after the final teardown, and the same two files of a kept topology removed by
            # `cleanup --apply` (descriptor-relative), all inside the operation's lock.
            ("os.unlink", "Controller.discard_topology_files"), ("os.unlink", "Controller.teardown_topology"),
            ("os.unlink", "Controller.cleanup_delete"),
        })
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        controller = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Controller")
        methods = {node.name: node for node in controller.body if isinstance(node, ast.FunctionDef)}
        for node in (functions["host_groups"], functions["inspect_editable_target"], functions["group_exists"],
                     methods["instance_deployed"], methods["deployed_evidence"], methods["describe_zone_data"]):
            with self.subTest(reader=node.name):
                self.assertEqual([install_write(call) for call in ast.walk(node) if isinstance(call, ast.Call)
                                  and install_write(call)], [])
        _, config_tree = parse_module("pf_config.py")
        for name in ("zone_status", "compiled_tzpath", "parse_admin_config", "plan_app_config", "rewrite_app_env"):
            node = next(item for item in config_tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
            with self.subTest(pure=name):
                self.assertEqual([install_write(call) for call in ast.walk(node) if isinstance(call, ast.Call)
                                  and install_write(call)], [])

        def callees(node):
            found = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == "self" \
                        and child.attr in methods:
                    found.add("Controller." + child.attr)
                elif isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in functions:
                    found.add(child.func.id)
            return found

        def node_of(name):
            return methods[name.split(".", 1)[1]] if name.startswith("Controller.") else functions[name]

        reachable, todo = set(), ["Controller." + name for name in methods
                                  if name in READ_ONLY_ROOTS or name.startswith(READ_ONLY_ROOT_PREFIXES)]
        todo += list(READ_ONLY_MODULE_ROOTS)
        while todo:
            name = todo.pop()
            if name not in reachable:
                reachable.add(name)
                todo += sorted(callees(node_of(name)) - reachable)
        self.assertIn("Controller.describe_zone_data", reachable)
        for writer in ("write_editable_file", "write_reviewed", "remove_editable_leftovers", "Controller.configure",
                       "Controller.write_config_change", "Controller.admin_wizard", "Controller.app_wizard"):
            self.assertNotIn(writer, reachable)

    def test_ss3b_installer_writes_are_allowlisted_and_unreachable_from_read_only_install_routes(self):
        _, tree = parse_module("pf_install.py")
        sites = {(name, where) for call, where in qualified_calls(tree) for name in [install_write(call)] if name}
        self.assertEqual(sites, INSTALL_WRITE_SITES)
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        methods = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods[node.name + "." + item.name] = (node.name, item)

        def callees(name):
            node = functions[name] if name in functions else methods[name][1]
            owner = methods[name][0] if name in methods else None
            found = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in functions:
                    found.add(child.func.id)
                elif isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id + ".__init__" in methods:
                    found.add(child.func.id + ".__init__")
                elif owner and isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) \
                        and child.value.id == "self" and owner + "." + child.attr in methods:
                    found.add(owner + "." + child.attr)
            return found

        self.assertTrue(set(INSTALL_READ_ONLY_ROOTS) <= set(functions))
        reachable, todo = set(), list(INSTALL_READ_ONLY_ROOTS)
        while todo:
            name = todo.pop()
            if name not in reachable:
                reachable.add(name)
                todo += sorted(callees(name) - reachable)
        self.assertFalse([name for name in reachable if name.startswith("_Run.")], sorted(reachable))
        self.assertNotIn("execute", reachable)
        self.assertNotIn("resume", reachable)
        written = {(name, where) for name, where in sites if where in reachable}
        self.assertEqual(written, set())

    def test_ss4_git_runs_only_as_a_version_probe_or_through_the_protected_store(self):
        _, tree = parse_module("pf-admin.py")
        sites = []
        for call, where in qualified_calls(tree):
            if isinstance(call.func, ast.Attribute) and call.func.attr == "command" and call.args \
                    and isinstance(call.args[0], ast.List) and call.args[0].elts \
                    and isinstance(call.args[0].elts[0], ast.Constant) and call.args[0].elts[0].value == "git":
                literal = [element.value if isinstance(element, ast.Constant) else None for element in call.args[0].elts]
                sites.append((where, literal))
        self.assertEqual(sorted(where for where, _ in sites), ["Controller.deploy", "Controller.doctor",
                                                               "Controller.store_git"])
        for where, literal in sites:
            if where != "Controller.store_git":
                # deploy keeps its pre-existing read-only availability probe (unchanged since PF-A1.2).
                self.assertEqual(literal, ["git", "--version"], where)

    def test_ss5_no_catch_all_remnant_remains(self):
        tokens = ("passthrough", "TIMEOUT_PASSTHROUGH", "compose-passthrough", "COMPOSE_MUTATING_VERBS",
                  "TOPOLOGY_GUARDED_COMMANDS", 'pending_route="compose:', "recoveries(project=")
        for name in RELEASE_MODULES:
            source = (PACKAGE / name).read_text(encoding="utf-8")
            if name == "pf_runner.py":
                # pf_runner.py is frozen by the PF-A1.4 contract; its stream docstring names the runner's
                # forwarding mode, not a route. Exactly that one occurrence is tolerated.
                self.assertEqual(source.count("passthrough"), 1)
                source = source.replace("(passthrough/logs)", "(logs)")
            for token in tokens:
                with self.subTest(module=name, token=token):
                    self.assertNotIn(token, source)
            tree = ast.parse(source, feature_version=(3, 9))
            for call, where in qualified_calls(tree):
                target = dotted(call.func) or ""
                if target.endswith("choose_recovery"):
                    self.assertNotIn("project", [keyword.arg for keyword in call.keywords])
                if target == "sys.stdin.isatty":
                    self.assertEqual(where, "unattended", name)
        admin = (PACKAGE / "pf-admin.py").read_text(encoding="utf-8")
        self.assertEqual(admin.count("sys.stdin.isatty("), 0)
        self.assertEqual(admin.count("stream.isatty()"), 1)
        self.assertEqual(pf.CHECKPOINT, "PF-A3.3")
        self.assertEqual(pf.VERSION, "2.5.0")

    def test_ss6_every_parser_refuses_abbreviations(self):
        result = pf.parser()
        self.assertIs(result.allow_abbrev, False)
        action = next(item for item in result._actions if isinstance(item, argparse._SubParsersAction))
        for name, subparser in action.choices.items():
            with self.subTest(subparser=name):
                self.assertIs(subparser.allow_abbrev, False)
        _, tree = parse_module("pf-admin.py")
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        parsers = [call for call in ast.walk(main) if isinstance(call, ast.Call)
                   and dotted(call.func) == "argparse.ArgumentParser"]
        self.assertEqual(len(parsers), 1)
        keywords = {keyword.arg: keyword.value for keyword in parsers[0].keywords}
        self.assertIsInstance(keywords.get("allow_abbrev"), ast.Constant)
        self.assertIs(keywords["allow_abbrev"].value, False)

    def test_ss7_shell_entry_points(self):
        launcher = (REPO_PACKAGE / "pf.sh").read_text()
        bootstrap_mode = launcher.split("# Legacy v2.5 control directory")[0]
        self.assertEqual(len([line for line in bootstrap_mode.splitlines()
                              if re.match(r"\s*exec\b", line)]), 1)
        self.assertEqual(len([line for line in launcher.splitlines() if re.match(r"\s*exec\b", line)]), 1)
        for name in ("backup.sh", "release-check.sh"):
            with self.subTest(wrapper=name):
                text = (PACKAGE / name).read_text()
                code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
                execs = [line.strip() for line in code_lines if re.search(r"\bexec\b", line)]
                self.assertEqual(len(execs), 2, execs)
                self.assertTrue(re.fullmatch(r'bootstrap\) exec "\$SELF_DIR/pf" .+ ;;', execs[0]), execs[0])
                self.assertTrue(re.fullmatch(r'control\) exec "\$SELF_DIR/pf\.sh" .+ ;;', execs[1]), execs[1])
                self.assertNotIn("`", text)
                for word in ("eval", "docker", "python", "python3"):
                    self.assertFalse([line for line in code_lines if re.search(r"\b" + word + r"\b", line)], word)
        # PF-A2.1 (E11): the thin init-only wrapper execs only the selected root-owned interpreter, isolated, with a
        # rebuilt environment; no eval, no removal, no file copy and no launcher writing in shell.
        installer = (PACKAGE / "install-control.sh").read_text()
        code_lines = [line for line in installer.splitlines() if not line.lstrip().startswith("#")]
        self.assertEqual([line.strip() for line in code_lines if re.search(r"\bexec\b", line)],
                         ['exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM="${TERM:-dumb}" "$PY" -I '
                          '-B "$SCRIPT_DIR/pf_install.py" "$@"'])
        self.assertNotIn("`", installer)
        for word in ("eval", "rm", "mv", "cp", "docker", "chmod", "chown"):
            self.assertFalse([line for line in code_lines if re.search(r"\b" + word + r"\b", line)], word)
        self.assertIn('[ -f "$candidate" ] && [ -x "$candidate" ] && [ -O "$candidate" ]', installer)
        for path in SHELL_ENTRY_POINTS:
            with self.subTest(script=path.name):
                result = subprocess.run(["sh", "-n", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, check=False, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)


# ============================================================================ CLI: installed launcher


@ROOT_REQUIRED
class InstalledLauncherRoutes(Base):
    """A1-T15/T18 through the installed launcher (E2): transcripts, zero transport, unchanged identities."""

    def launcher(self, arguments, *, interactive=False, env=None):
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        environment.update(env or {})
        with (pfx.interactive_stdin() if interactive else contextlib.nullcontext(subprocess.DEVNULL)) as stdin:
            return subprocess.run([str(self.layout.launcher), *arguments], env=environment,
                                  cwd=str(self.layout.root.parent), stdin=stdin, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, check=False, timeout=300)

    def hostile(self):
        """Marker executables and an import hook in every caller-controlled channel (A1-T07 style)."""
        self.marker = self.base / "injection-marker"
        hostile_bin = self.base / "hostile-bin"
        hostile_bin.mkdir(exist_ok=True)
        for name in ("docker", "docker-compose", "python3", "git", "sh"):
            path = hostile_bin / name
            path.write_text(f"#!/bin/sh\necho executed > {self.marker}\nexit 0\n")
            os.chmod(path, 0o755)
        python_dir = self.base / "hostile-python"
        python_dir.mkdir(exist_ok=True)
        (python_dir / "sitecustomize.py").write_text(f"open({str(self.marker)!r}, 'w').write('executed')\n")
        return {"PATH": f"{hostile_bin}:/usr/bin:/bin", "PYTHONPATH": str(python_dir), "DOCKER_HOST": "tcp://127.0.0.1:1",
                "COMPOSE_FILE": str(self.base / "hostile.yaml"), "COMPOSE_PROJECT_NAME": "hostile"}

    def transcript(self, arguments, result, before):
        return {"argv": ["pf", *arguments], "exit": result.returncode, "stdout": result.stdout,
                "stderr": result.stderr, "resource_identities_before": before,
                "resource_identities_after": self.identities(), "fake_daemon_calls": self.fake.calls(),
                "security_markers": {"injection_marker_present": self.marker.exists() if hasattr(self, "marker")
                                     else None}}

    def test_cli1_removed_routes_through_the_installed_launcher(self):
        env = self.hostile()
        transcripts = {}
        for arguments, code in (
                (["--instance", "staging", "up", "-d"], "compose-route-removed"),
                (["--instance", "staging", "exec", "db", "sh"], "compose-route-removed"),
                (["--instance", "staging", "run", "--rm", "backend", "sh"], "compose-route-removed"),
                (["--instance", "staging", "down", "-v"], "compose-route-removed"),
                (["--instance", "staging", "rm", "-f"], "compose-route-removed"),
                (["--instance", "staging", "-f", "/tmp/x.yaml", "ps"], "compose-override-refused"),
                (["--instance", "staging", "config"], "compose-route-removed")):
            with self.subTest(arguments=arguments):
                before = self.identities()
                tree = pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state)
                result = self.launcher(arguments, env=env)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("ERROR: " + code + ":", result.stderr)
                self.assertEqual(self.fake.calls(), [])
                self.assertEqual(self.operation_dirs(), [])
                self.assertEqual(pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state), tree)
                self.assertEqual(self.identities(), before)
                self.assertFalse(self.marker.exists(), "injected code executed")
                transcripts[" ".join(arguments)] = self.transcript(arguments, result, before)
        record_evidence("CLI-1", transcripts)

    def test_cli2_ps_and_logs_through_the_installed_launcher(self):
        state = self.fake.state()
        state["compose"]["logs"] = "backend-1 | password abc123 in a log line\n"
        self.fake.write_state(state)
        transcripts = {}
        for arguments, verb in ((["--instance", "staging", "ps"], ["ps"]),
                                (["--instance", "staging", "logs", "--tail", "5", "backend"],
                                 ["logs", "--tail", "5", "backend"])):
            with self.subTest(arguments=arguments):
                self.fake.clear_calls()
                before = self.identities()
                result = self.launcher(arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = self.fake.calls()
                self.assertEqual([call["argv"] for call in calls],
                                 [PROBE, ["compose", "version"], self.compose_prefix() + verb])
                self.assertEqual({call["DOCKER_HOST"] for call in calls}, {self.context.daemon.endpoint})
                self.assertNotIn("abc123", result.stdout + result.stderr)
                self.assertEqual(self.operation_dirs(), [])
                transcripts[" ".join(arguments)] = self.transcript(arguments, result, before)
        self.assertIn(pf.pf_runner.REDACTED, transcripts["--instance staging logs --tail 5 backend"]["stdout"])
        record_evidence("CLI-2", transcripts)

    def test_cli3_unknown_words_options_and_the_ps_alias(self):
        transcripts = {}
        for arguments, code, expected in ((["frobnicate"], 1, "ERROR: unknown-command:"),
                                          (["--inst", "staging", "status"], 1, "ERROR: unknown-option:"),
                                          ([], 0, None)):
            with self.subTest(arguments=arguments):
                self.fake.clear_calls()
                before = self.identities()
                result = self.launcher(arguments)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                if expected:
                    self.assertIn(expected, result.stderr)
                    self.assertEqual(self.fake.calls(), [])
                else:
                    self.assertEqual(self.fake.argvs()[-1], self.compose_prefix() + ["ps"])
                transcripts[" ".join(arguments) or "(none)"] = self.transcript(arguments, result, before)
        record_evidence("CLI-3", transcripts)

    def test_cli5_backup_consumes_the_frozen_configuration_after_an_edit(self):
        """A1-T18 cli_gate: `backup` freezes config/.env once in begin_operation and never re-freezes; an
        edit after the freeze (on the first Compose call) reaches no child."""
        env_path = self.paths["configuration"] / ".env"
        original = env_path.read_text()
        edited = original.replace("POSTGRES_PASSWORD=abc123", "POSTGRES_PASSWORD=changed-after-freeze-77")
        state = self.fake.state()
        state["hooks"] = [{"after_argv_prefix": ["compose"], "nth": 1,
                           "mutate": [{"op": "write_file", "path": str(env_path), "text": edited}]}]
        self.fake.write_state(state)
        frozen = pf.pf_config.parse_app_env(original.encode(), label=".env")
        frozen_url = pf.pf_config.child_values(frozen, workspace=self.context.paths.workspace,
                                               instance_id=self.context.instance_id)["PARTFLOW_DATABASE_URL"]
        before = self.identities()
        result = self.launcher(["--instance", "staging", "backup"], interactive=True)
        compose_calls = [call for call in self.fake.calls() if call["argv"][:1] == ["compose"]]
        after_edit = [call for call in compose_calls[1:]]
        self.assertTrue(after_edit, result.stdout + result.stderr)
        expected = pf_instance.sha256_bytes(frozen_url.encode("utf-8"))
        self.assertEqual({call["database_url_sha256"] for call in after_edit}, {expected})
        self.assertNotIn("changed-after-freeze-77", self.fake.calls_path.read_text() + result.stdout + result.stderr)
        operations = self.operation_dirs()
        self.assertEqual(len(operations), 1)
        snapshot = self.context.operations_dir / operations[0] / "app.env"
        self.assertEqual(pf.pf_config.parse_app_env(snapshot.read_bytes(), label="snapshot"), frozen)
        self.assertEqual(env_path.read_text(), edited)
        record_evidence("CLI-5", self.transcript(["--instance", "staging", "backup"], result, before))

    def test_cli6_unattended_default_selection_is_refused(self):
        before = self.identities()
        result = self.launcher(["backup"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR: instance-required-unattended: 'backup' is running without a terminal and selected "
                      "instance staging by single registration.", result.stderr)
        self.assertEqual(self.fake.calls(), [])
        self.assertEqual(self.operation_dirs(), [])
        record_evidence("CLI-6", self.transcript(["backup"], result, before))

    def test_cli7_unattended_policy_and_terminal_refusals(self):
        transcripts = {}
        for arguments, code, expected in ((["--instance", "staging", "backup"], 20, "ERROR: policy-grant-required:"),
                                          (["--instance", "staging", "update", "--latest"], 1,
                                           "ERROR: terminal-required:")):
            with self.subTest(arguments=arguments):
                before = self.identities()
                result = self.launcher(arguments)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertEqual(self.fake.calls(), [])
                self.assertEqual(self.operation_dirs(), [])
                transcripts[" ".join(arguments)] = self.transcript(arguments, result, before)
        record_evidence("CLI-7", transcripts)


if __name__ == "__main__":
    unittest.main()
