"""L4 rows beyond the update/backup/rollback/reset core of matrix.py: purge, restore-instance, deploy, cleanup,
abort-deploy, resume, timeouts, sessions, ballast, chattr, workspace renames and the journal-less rows."""
import json
import os
from pathlib import Path
import subprocess

from . import checks
from . import faults
from . import instances as instances_module
from . import matrix
from . import phrases
from . import util

IMMUTABLE_LIST = Path("/run/pfa34/immutable.list")


# ------------------------------------------------------------------------------------------------ subjects


def purge_phrases(inst):
    env = inst.env()
    return phrases.exact("PURGE " + inst.project, "DELETE " + env["POSTGRES_DB"]) + \
        (r"ERASE " + inst.project + r" [0-9A-F]{6}",)


def cmd_purge(h, inst, context):
    return ["purge", "--keep-backups"], purge_phrases(inst), ()


def restore_phrases(h, inst, bundle):
    db = h.fixture.get("alpha_db") or "partflow_staging"
    return phrases.exact("RESTORE INSTANCE " + inst.project, "RESTORE " + db + " " + bundle)


def latest_bundle(h, inst):
    names = []
    for name, plan, journal in checks.operations(inst):
        if plan.get("kind") == "purge":
            names += [item["name"] for item in journal.get("retained_artifacts") or []
                      if item.get("kind") == "purge-bundle"]
    return names[-1] if names else None


def is_purged(h, inst):
    return (h.instance_record(inst.slug) or {}).get("state") == "purged"


def bring_back(h, inst, context=None):
    """After a purge row: restore the newest bundle so the next rows have a running subject (documented route)."""
    instances_module.wait_pf_idle(timeout=300)
    if h.open_operation(inst.slug) is not None:
        matrix.recover(h, inst, h.open_operation(inst.slug)["operation_id"], row="post-restore")
    if is_purged(h, inst):
        bundle = latest_bundle(h, inst)
        inst.pf(["restore-instance", bundle], step_id=f"post-restore-{util.utc()}",
                phrases=restore_phrases(h, inst, bundle), timeout=7200)
        inst.info["purged"] = False
        h.save()


def prepare_running(h, inst):
    instances_module.wait_pf_idle(timeout=300)
    if is_purged(h, inst):
        bring_back(h, inst)
    if h.open_operation(inst.slug) is not None:
        matrix.recover(h, inst, h.open_operation(inst.slug)["operation_id"], row="pre")
    matrix.ensure_head(h, inst, (inst.heads() or ["0032"])[0][:4])
    return {}


def prepare_purged(h, inst):
    """A purged subject for the restore-instance rows: a normal purge first (recorded as a setup step)."""
    prepare_running(h, inst)
    inst.pf(["purge", "--keep-backups"], step_id=f"pre-purge-{util.utc()}", phrases=purge_phrases(inst),
            timeout=7200)
    inst.info["purged"] = True
    h.save()
    return {"bundle": latest_bundle(h, inst)}


def cmd_restore(h, inst, context):
    bundle = context["bundle"]
    return ["restore-instance", bundle], restore_phrases(h, inst, bundle), ()


REPLACEMENTS = {"n": 0}


def prepare_fresh(h, inst_unused):
    """A fresh replacement subject alpha-r<n> (project pfr<n>) registered and configured; not deployed."""
    REPLACEMENTS["n"] = h.fixture.setdefault("replacements", 0) + 1
    h.fixture["replacements"] = REPLACEMENTS["n"]
    slug = f"alpha-r{REPLACEMENTS['n']}"
    h.instances[slug] = {"spec": instances_module.replacement_spec(REPLACEMENTS["n"])}
    fresh = h.instance(slug)
    fresh.register()
    return {"subject": fresh}


def cmd_deploy(h, inst, context):
    sha = h.commit("OLD")
    return ["deploy", "--commit", sha, "--skip-ci"], phrases.exact("DEPLOY " + sha[:12]), instances_module.REUSE_ENV


# ------------------------------------------------------------------------------------------------- faults


class Session:
    """A harness PostgreSQL session held open inside a db container (I5/I5b/R46/R53): ``statements`` run inside one
    open transaction; ``close()`` ends it (recorded)."""

    def __init__(self, h, inst, database, statements, label):
        env = inst.env()
        self.h, self.label = h, label
        self.process = subprocess.Popen(
            [util.DOCKER, "exec", "-i", inst.db_container(), "psql", "-X", "-q", "-U", env["POSTGRES_USER"], "-d",
             database], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(util.BASE_ENV))
        self.process.stdin.write(("BEGIN;\n" + "".join(item + "\n" for item in statements)).encode())
        self.process.stdin.flush()
        h.external_event("session-open", f"psql -d {database}: BEGIN; {' '.join(statements)}", note=label)

    def close(self):
        try:
            out, err = self.process.communicate(input=b"ROLLBACK;\n\\q\n", timeout=60)
        except subprocess.TimeoutExpired:
            self.process.kill()
            out, err = self.process.communicate()
        except (OSError, ValueError):  # psql already ended (its session was terminated): collect what remains
            self.process.wait(timeout=60)
            err = b""
        self.h.external_event("session-close", f"psql session {self.label} closed", note=err.decode()[-200:])
        return err.decode()[-400:]


def ballast(path, keep_free):
    stats = os.statvfs(str(path))
    free = stats.f_bavail * stats.f_frsize
    target = Path(path) / ".pfa34-ballast"
    size = max(0, free - keep_free)
    util.run(["fallocate", "-l", str(size), str(target)], timeout=600)
    return target, size


def chattr(path, flag):
    result = util.run(["chattr", flag, str(path)], timeout=60)
    if flag == "+i":
        with open(str(IMMUTABLE_LIST), "a", encoding="utf-8") as handle:
            handle.write(str(path) + "\n")
    return result.returncode


def first_destroy_gate(h, slug, project, resource_type=None):
    """The purge reached its deletion (journal phase deleting) and the daemon reported the first destroy (of a
    ``resource_type`` object, such as ``volume``, when given) since the injected command started."""
    holder = {}

    def predicate():
        started = faults.command_anchor(holder, h.operations_dir(slug))
        operation, journal = faults.journal_state(h.operations_dir(slug))
        if not journal or journal.get("phase") != "deleting":
            return None
        for item in h.events_between(started):
            attributes = (item.get("Actor") or {}).get("Attributes") or {}
            if resource_type and item.get("Type") != resource_type:
                continue
            owned = attributes.get("com.docker.compose.project") == project or \
                (resource_type == "volume" and str(attributes.get("name") or (item.get("Actor") or {}).get("ID")
                                                   or "").startswith(project + "_"))
            if (item.get("Action") or "").startswith("destroy") and owned:
                return {"operation": operation, "destroy": (item.get("Actor") or {}).get("ID"), "phase": "deleting"}
        return None
    return predicate


# ----------------------------------------------------------------------------------------------- the table


def rows(h, inst):
    table = []

    def add(row_id, kind, phase, effect_type, injection, spec, *, prepare, command, grid=(), when="after", **extra):
        value = {"id": row_id, "kind": kind, "phase": phase, "effect_type": effect_type, "injection": injection,
                 "spec": spec, "grid": [list(cell) for cell in grid], "when": when, "prepare": prepare,
                 "command": command}
        value.update(extra)
        table.append(value)

    # ---- purge (subject alpha; bring_back after each row)
    add("R24", "purge", "preserving", "service-change", "I3 SIGTERM at phase preserving",
        {"primitive": "I3", "signal": "SIGTERM", "gate": matrix.phase_trigger(h, inst.slug, "preserving")},
        prepare=prepare_running, command=cmd_purge, when="before", post=bring_back, data_relation="restored")
    add("R25", "purge", "capturing", "capture", "I1 SIGKILL on docker image save",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["image", "save"],
         "gate": matrix.phase_trigger(h, inst.slug, "capturing")},
        prepare=prepare_running, command=cmd_purge, when="before", post=bring_back, data_relation="restored")
    add("R26", "purge", "verifying", "verification", "I1 SIGKILL on pg_restore into the pfverify topology",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_restore", "pfverify-"],
         "gate": matrix.phase_trigger(h, inst.slug, "verifying")},
        prepare=prepare_running, command=cmd_purge, post=bring_back, data_relation="restored")
    add("R27", "purge", "deleting", "resource-delete", "I3 + docker events: SIGKILL after the first destroy",
        {"primitive": "I3", "signal": "SIGKILL", "gate": first_destroy_gate(h, inst.slug, inst.project)},
        prepare=prepare_running, command=cmd_purge, grid=[("deletion", "SIGKILL")], post=bring_back,
        data_relation="restored")
    add("R54", "purge", "deleting", "resource-delete", "I3 SIGTERM after the first destroy",
        {"primitive": "I3", "signal": "SIGTERM", "gate": first_destroy_gate(h, inst.slug, inst.project)},
        prepare=prepare_running, command=cmd_purge, grid=[("deletion", "SIGTERM")], post=bring_back,
        data_relation="restored")
    add("R55", "purge", "deleting", "resource-delete", "I6 daemon restart after the first destroy",
        {"primitive": "I6", "gate": first_destroy_gate(h, inst.slug, inst.project)},
        prepare=prepare_running, command=cmd_purge, grid=[("deletion", "restart")], post=bring_back,
        data_relation="restored")
    add("R52", "purge", "deleting", "resource-delete", "I4 SIGKILL of the first docker rm / volume rm client",
        {"primitive": "I4", "child": ["docker", " rm "], "gate": matrix.phase_trigger(h, inst.slug, "deleting")},
        prepare=prepare_running, command=cmd_purge, grid=[("deletion", "error exit")], post=bring_back,
        data_relation="restored", when="before")
    add("R29", "purge", "finalizing", None, "I3 SIGKILL at phase finalizing",
        {"primitive": "I3", "signal": "SIGKILL", "gate": matrix.phase_trigger(h, inst.slug, "finalizing")},
        prepare=prepare_running, command=cmd_purge, post=bring_back, data_relation="restored")
    # ---- restore-instance (subject purged first)
    add("R30", "restore-instance", "preparing-target", None, "I3 SIGKILL at phase preparing-target",
        {"primitive": "I3", "signal": "SIGKILL", "gate": matrix.phase_trigger(h, inst.slug, "preparing-target")},
        prepare=prepare_purged, command=cmd_restore, post=bring_back, data_relation="restored")
    add("R32", "restore-instance", "preparing-target", "image-load", "I2 on docker image load",
        {"primitive": "I2", "child": ["image", "load"],
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="image-load")},
        prepare=prepare_purged, command=cmd_restore, grid=[("journal commit", "SIGKILL")], post=bring_back,
        data_relation="restored")
    add("R31", "restore-instance", "restoring-data", "database-restore", "I1 SIGKILL on pg_restore",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_restore"],
         "gate": matrix.phase_trigger(h, inst.slug, "restoring-data")},
        prepare=prepare_purged, command=cmd_restore, post=bring_back, data_relation="restored", when="before")
    add("R33", "restore-instance", "activating", "service-change", "I6 daemon restart during activation",
        {"primitive": "I6", "gate": matrix.phase_trigger(h, inst.slug, "activating")},
        prepare=prepare_purged, command=cmd_restore, post=bring_back, data_relation="restored", when="before")
    add("R64", "restore-instance", "syncing-workspace", "source-switch", "I3 SIGKILL at source-switch started",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="source-switch")},
        prepare=prepare_purged, command=cmd_restore, grid=[("workspace rename", "SIGKILL")], post=bring_back,
        data_relation="restored", when="before")
    # ---- update/rollback faults
    add("R46", "rollback", "switching", "database-switch",
        "error exit: a harness session on the current database at the switch",
        {"primitive": "I7", "gate": matrix.effect_trigger(h, inst.slug, effect_type="database-restore",
                                                          states=("complete",)),
         "act": lambda process, target: _open_current_session(h, inst)},
        prepare=matrix.prepare_rollback, command=matrix.cmd_rollback, grid=[("DB switch", "error exit")],
        data_relation="restored", after_inject=_close_sessions, when="before")
    add("R47", "reset-db", "switching", "database-switch", "I5b lock_timeout on pg_database",
        {"primitive": "I7", "gate": matrix.effect_trigger(h, inst.slug, effect_type="database-migrate",
                                                          target_prefix="database:pf_clean_", states=("complete",)),
         "act": lambda process, target: _open_catalog_lock(h, inst)},
        prepare=prepare_running, command=matrix.cmd_reset, grid=[("DB switch", "timeout")], data_relation="restored",
        after_inject=_close_sessions, post=matrix.restore_data_after_reset, when="before")
    add("R50", "update", "syncing-workspace", "source-switch", "error exit: chattr +i on the workspace parent",
        {"primitive": "I7", "gate": matrix.effect_trigger(h, inst.slug, effect_type="source-switch"),
         "act": lambda process, target: _immutable_parent(h, inst)},
        prepare=matrix.prepare_update, command=matrix.cmd_update, grid=[("workspace rename", "error exit")],
        after_inject=_mutable_parent, when="before")
    add("R51", "rollback", "syncing-workspace", "source-switch", "I8 SIGTERM+SIGCONT at the first rename event",
        {"primitive": "I8", "signal": "SIGTERM"}, prepare=matrix.prepare_rollback, command=matrix.cmd_rollback,
        grid=[("workspace rename", "SIGTERM")], data_relation="restored", attempts=3)
    add("R05", "update", "migrating", "database-migrate", "I5 real timeout on the live migration",
        {"primitive": "I7", "gate": matrix.effect_trigger(h, inst.slug, effect_type="database-migrate",
                                                          target_prefix="database:pf_migrate_", states=("complete",)),
         "act": lambda process, target: _audit_lock(h, inst)},
        prepare=matrix.prepare_update, command=matrix.cmd_update, grid=[("migrations", "timeout")],
        setup_fault=lambda h, inst, context, state: _timeout_data(h, 60),
        teardown_fault=lambda h, inst, context, state: _timeout_data(h, None), after_inject=_close_sessions,
        timeout=1800)
    add("R10", "backup", "capturing", "capture", "I7 docker kill of the db during pg_dump",
        {"primitive": "I7", "child": ["pg_dump"], "gate": matrix.effect_trigger(h, inst.slug, effect_type="capture"),
         "act": lambda process, target: _kill_db(h, inst)},
        prepare=prepare_running, command=matrix.cmd_backup, when="before", expect=_next_backup)
    add("R13", "backup", "capturing", "capture", "I7 ballast fills the backups device at capturing (ENOSPC)",
        {"primitive": "I7", "gate": matrix.effect_trigger(h, inst.slug, effect_type="capture"),
         "act": lambda process, target: _ballast_backups(h, inst)},
        prepare=prepare_running, command=matrix.cmd_backup, after_inject=_remove_ballast, when="before")
    add("R45", "reset-db", "initializing", "database-migrate", "I2 on the candidate alembic upgrade (pf_clean_*)",
        {"primitive": "I2", "child": ["alembic", "upgrade"],
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="database-migrate",
                                       target_prefix="database:pf_clean_")},
        prepare=prepare_running, command=matrix.cmd_reset, grid=[("migrations", "SIGKILL")],
        data_relation="restored", post=matrix.restore_data_after_reset)
    add("R62", "reset-db", "finalizing", "file-write", "I3 SIGKILL at last-reset file-write started",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="file-write", target_prefix="last-reset")},
        prepare=prepare_running, command=matrix.cmd_reset, data_relation="restored",
        post=matrix.restore_data_after_reset, when="before")
    # ---- deploy (fresh replacement subjects)
    add("R34", "deploy", "initializing", "database-migrate", "I2 on the initial alembic upgrade",
        {"primitive": "I2", "child": ["alembic", "upgrade"]}, prepare=prepare_fresh, command=cmd_deploy,
        grid=[("migrations", "SIGKILL")], subject="fresh", gate_kind="database-migrate")
    add("R35", "deploy", "activating", "service-change", "I1 SIGKILL during activation",
        {"primitive": "I3", "signal": "SIGKILL"}, prepare=prepare_fresh, command=cmd_deploy, subject="fresh",
        gate_phase="activating", when="before")
    add("R63", "deploy", "syncing-workspace", "source-switch", "I3 SIGKILL at source-switch started",
        {"primitive": "I3", "signal": "SIGKILL"}, prepare=prepare_fresh, command=cmd_deploy, subject="fresh",
        gate_kind="source-switch", grid=[("workspace rename", "SIGKILL")], when="before")
    return table


# --------------------------------------------------------------------------------------- fault actions


SESSIONS = []


def _open_current_session(h, inst):
    session = Session(h, inst, inst.env()["POSTGRES_DB"], ["SELECT 1;"], "R46 current-database session")
    SESSIONS.append(session)
    return {"session": "current database"}


def _open_catalog_lock(h, inst):
    session = Session(h, inst, "postgres", ["LOCK TABLE pg_catalog.pg_database IN SHARE MODE;"],
                      "R47 I5b pg_database SHARE lock")
    SESSIONS.append(session)
    return {"session": "pg_database SHARE"}


def _audit_lock(h, inst):
    session = Session(h, inst, inst.env()["POSTGRES_DB"], ["LOCK TABLE audit_events IN ACCESS SHARE MODE;"],
                      "R05 I5 audit_events ACCESS SHARE")
    SESSIONS.append(session)
    return {"session": "audit_events ACCESS SHARE"}


def _close_sessions(h, inst, context, state):
    errors = []
    while SESSIONS:
        errors.append(SESSIONS.pop().close())
    state["session_errors"] = errors


def _timeout_data(h, seconds):
    settings = json.loads(h.settings_path.read_text(encoding="utf-8"))
    settings["timeout_data"] = seconds
    util.write_private(h.settings_path, json.dumps(settings, indent=1, sort_keys=True) + "\n", mode=0o644)
    h.external_event("settings", f"timeout_data={seconds}", target=str(h.settings_path),
                     note="fixture release-source settings (SPEC 3.3 I5)")


def _next_backup(h, inst, context, recovery):
    """R10: the next backup succeeds through a pf route. Records the first attempt (before any manual step) and,
    when pf names no route to start the killed db, the operator's manual start and the backup after it."""
    status = inst.pf(["status"], step_id="R10-status-after", expect_exit=None)
    first = inst.pf(["backup"], step_id="R10-next-backup", expect_exit=None, allow_fail=True)
    checks_list = [{"check": "R10: the next backup succeeds without a manual step", "ok": first.exit == 0,
                    "detail": first.stderr[-300:]}]
    if first.exit != 0:
        routed = "docker start" in status.stdout or "start" in " ".join(
            " ".join(args) for args, _ in matrix.parse_routes(status.stdout, inst.slug))
        checks_list.append({"check": "R10: pf names a route that starts the stopped db", "ok": routed,
                            "detail": "status names no route; dead end (F-A34-03)"})
        matrix.start_stopped_db(h, inst)
        second = inst.pf(["backup"], step_id="R10-next-backup-after-manual-start", expect_exit=None, allow_fail=True)
        checks_list.append({"check": "R10: the backup succeeds after the operator's manual db start",
                            "ok": second.exit == 0, "detail": second.stderr[-300:], "informational": True})
    return checks_list


def _kill_db(h, inst):
    container = inst.db_container()
    h.external_event("I7", f"docker kill {container[:12]} (db during pg_dump)", target="db")
    util.docker(["kill", container])
    return {"killed": container}


BALLAST = []


def _ballast_backups(h, inst):
    target, size = ballast(inst.backups, 4 * 1024 * 1024)
    BALLAST.append(target)
    h.external_event("I7-ballast", f"fallocate -l {size} {target}", target=str(target))
    return {"ballast": str(target), "bytes": size}


def _remove_ballast(h, inst, context, state):
    while BALLAST:
        target = BALLAST.pop()
        if target.exists():
            target.unlink()
        h.external_event("I7-ballast-removed", f"rm {target}", target=str(target))


def _immutable_parent(h, inst):
    parent = inst.workspace.parent
    code = chattr(parent, "+i")
    h.external_event("I7-chattr", f"chattr +i {parent}", target=str(parent), note=f"exit {code}")
    return {"chattr": code}


def _mutable_parent(h, inst, context, state):
    parent = inst.workspace.parent
    code = chattr(parent, "-i")
    h.external_event("I7-chattr", f"chattr -i {parent}", target=str(parent), note=f"exit {code}")
