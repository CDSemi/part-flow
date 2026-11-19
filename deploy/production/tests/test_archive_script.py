"""Movement-history archival, build steps 1 and 2 (the purge): deploy/production/archive.sh against the fake executables
of test_backup_scripts.py (docker, df, ps, uname, id, curl, sleep) plus a fake `date` that supplies the clock and a stub
backup.sh beside the script copy; the archive lock seen from release.sh (the release.sh harness of
test_release_scripts.py); `pf archive` (the pf harness of test_pf_script.py, beside a stub archive.sh); and the
archival_proposal line of check.sh (the check.sh harness of test_check_script.py, with the real monitor_report.py).
Cases:
  AR-1..AR-4   the hourly work: the steps `next` names in one start (export, verify, copy, the pre-purge backup, the
               purge, the reconcile after it, status) and the ARCHIVE line
  AR-5..AR-9   exit codes 1, 2 and 3 and the records that are kept
  AR-10..AR-12 the optional copy folder: mounted only when usable and on another file system
  AR-13..AR-17 --scheduled: the hourly stamp, the Start minute from `history-archive settings`, once per hour
  AR-18..AR-23 the locks in both directions (archive.sh refuses while a release runs; release.sh refuses while the
               archive lock is held; a stale archive lock; WAITING_FOR_COPY never holds the lock)
  AR-24        pf archive
  AR-25..AR-28 check.sh archival_proposal from `history-archive status`
  AR-29..AR-39 step 2: the pre-purge backup and its wait, a backup failure, a resumed PURGING run, exit 3 from `next`,
               a failed purge, the reconcile after a purge and its incident record, an interruption, the receipts of
               recover, a step that makes no progress, and the starts that fail closed while the incident record
               exists (manual, through `pf archive`, scheduled)
  AR-40..AR-42 check.sh: receipt_not_copied, the incident record, a PURGING and a COMPLETED_WITH_SKIPS run
  AR-43..AR-56 step 3, permanent deletion requests: the cheap check at every scheduled start (a request open or not,
               the check without an answer), the deletion order and its final line, the slot left to the next start,
               every exit of start-deletion, the pre-purge backup and delete (interrupted, an unknown outcome resolved by
               status both ways, failed, refused), the reconcile after a deletion and its incident record, a request
               that waits while a run is RUNNING, a request failed by recover, an interruption during the deletion,
               the lock while a deletion runs, and check.sh's deletion_request_waiting and the deletion incident
  AR-58..AR-64 step 4, automatic runs: `evaluate` in the hourly work after recover and before the `next` loop (each
               decision and its EVALUATE line), an APPROVED automatic run worked on in the same start, a PROPOSED run
               waiting, exit 1 and 2 of evaluate going on with the loop, no evaluate in a deletion-only start or while
               the incident record exists, the help, and check.sh's approval_overdue, authorization_failed and
               standing_authorization_invalid lines
  AR-65..AR-76 step 5, viewing an archive: the cheap check's viewing work at any start (no evaluate, no run work, the
               slot unmarked), close_view and load_view after the deletion work and before the run work, the VIEW line,
               a failed load (exit 3), an interrupted load or closing (exit 2), a refusal (exit 1), no valid report, no
               progress, a signal, recover's failed views, no viewing work while the incident record exists, the lock
               held, the help, check.sh's views and view_not_closed (AR-75), and a view that changed after `next`
               handed it out, which a start skips (AR-76)
Nothing touches a Docker daemon, a database or the network.

Run with the other production tests:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
"""
import calendar
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backup_scripts import ScriptHarness  # noqa: E402
from test_check_script import EPOCH as CHECK_EPOCH, CheckHarness, df_text  # noqa: E402
from test_monitor_report import reconcile_doc  # noqa: E402
from test_pf_script import PF, PfHarness  # noqa: E402
from test_release_scripts import (  # noqa: E402
    RELEASE_SH, SH_REQUIRED, Harness as ReleaseHarness, find_sh, out, rule, sh_path,
)

REPO = Path(__file__).resolve().parents[3]
ARCHIVE_SH = REPO / "deploy" / "production" / "archive.sh"
# Git Bash may rewrite the -v value for a native fake (as a path list): match it loosely.
MAINT = r"--profile ops run --rm --no-deps -T --user 1000:1000 (-v .+ )?maintenance "
MAINTENANCE_PS = (r"^ps -q --filter label=com\.docker\.compose\.project=partflow-production"
                  r" --filter label=com\.docker\.compose\.service=maintenance$")
# The reconcile after a purge: a one-off backend container (the application role), as release.sh runs it.
RECONCILE = r" run --rm --no-deps -T backend python -m app\.cli reconcile --max-findings 10000$"
# The cheap check of every scheduled start (step 3): history-archive settings through `exec` in the running backend.
CHEAP = r" exec -T backend python -m app\.cli history-archive settings$"
# 2026-10-08T12:00:00Z, a whole hour (the fake clock is UTC, so the local minute is the UTC minute).
BASE = calendar.timegm((2026, 10, 8, 12, 0, 0))
RUN = 3
BOOT = "boot-a"
ARCHIVE_NAME = "run-3-20261008T122000Z"
BACKUP = f"20261008T122030Z-pre-purge-{ARCHIVE_NAME}"
DUMP_STARTED = "2026-10-08T12:20:31Z"
PURGE = MAINT + f"purge --run {RUN}"
PURGE_WITH_BACKUP = f"purge --run {RUN} --backup {BACKUP} --backup-started-at {DUMP_STARTED}"
BACKUP_ARGUMENTS = ["--kind", "pre-purge", "--label", ARCHIVE_NAME, "--operator", "archive.sh", "--reason",
                    f"pre-purge backup for {ARCHIVE_NAME}", "--env-file", ".env.production", "--lock-wait-minutes"]
REQUEST_NEW = "Request a new run in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."
REQUEST_AGAIN = "Request the deletion again in PartFlow, or with pf delete-permanently (OPERATIONS_RUNBOOK §10)."
# The refusal of a start while the incident record of a reconcile after a purge exists (after the record's path).
INCIDENT_REFUSAL = (f" of a reconcile after a purge exists (run {RUN}, result mismatch): no run, deletion request or view"
                    " is worked on while it exists, no evaluation of the automatic runs, export, verification, copy,"
                    " pre-purge backup, purge, deletion, load or closing of a view, not even the resumed purge of a run"
                    " left PURGING. Follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge); once the"
                    " investigation is done and the reconciliation is clean, remove the record (rm ")
# Step 3: permanent deletion request 7, its pre-purge backup and the deletion.
REQ = 7
DEL_LABEL = "deletion-7-20261008T122031Z"
DEL_BACKUP = f"20261008T122032Z-pre-purge-{DEL_LABEL}"
DEL_DUMP = "2026-10-08T12:20:33Z"
DELETE_WITH_BACKUP = f"delete --request {REQ} --backup {DEL_BACKUP} --backup-started-at {DEL_DUMP}"
DEL_BACKUP_ARGUMENTS = ["--kind", "pre-purge", "--label", DEL_LABEL, "--operator", "archive.sh", "--reason",
                        f"pre-purge backup for deletion request {REQ}", "--env-file", ".env.production",
                        "--lock-wait-minutes", "60"]
DELETION_ORDER = ["recover", "next", f"start-deletion --request {REQ}", "next", "backup.sh", DELETE_WITH_BACKUP,
                  "reconcile", "status"]
# Step 4: a start due for the hourly work (a manual start, or a scheduled start at its slot) evaluates the automatic runs
# after recover, also when a deletion request then comes first.
DELETION_ORDER_DUE = ["recover", "evaluate", *DELETION_ORDER[1:]]
# The messages of `history-archive evaluate` (contract B4 §3.1), for run 3 where a decision names a run.
EVALUATE_MESSAGES = {
    "off": "Automatic runs are off (History archival & purge), so no run was created.",
    "no_retention_period": "No retention period is set, so no history is eligible and no run was created.",
    "run_open": f"Run {RUN} is still open (WAITING_FOR_COPY); no automatic run is created while a run is open.",
    "not_due": ("The last automatic run, run 2, ended at 2026-10-05T12:00:00.000000Z; the next automatic run is due from"
                " 2026-10-12T12:00:00.000000Z (7 days between automatic runs)."),
    "authorization_invalid": ("The User who switched off the approval step of automatic runs, Ann (user 4), no longer"
                              " holds Run history archival and purge. No automatic run is created until a holder of the"
                              " key saves the approval setting again."),
    "below_limit": "Live Movement rows 4,000 are not above the limit 5,000,000, so no run was created.",
    "nothing_eligible": "Nothing is eligible before 2024-10-08 (retention 24 months), so no run was created.",
    "settings_changed": ("The settings of automatic runs or the retention period changed while the evaluation ran, so no"
                         " run was created; the next hourly work evaluates again."),
    "created": (f"Run {RUN} was proposed: Live Movement rows 5,120,334 above the limit 5,000,000; retention 24 months. It"
                " waits for a holder of Run history archival and purge to approve or dismiss it."),
}
AUTOMATIC_REASON = "Live Movement rows 5,120,334 above the limit 5,000,000; retention 24 months"
CREATED_APPROVED = (f"Run {RUN} was created APPROVED under the standing authorization of audit row 12:"
                    f" {AUTOMATIC_REASON}. The host job works on it without approval.")
TIMED_OUT = ("The evaluation of automatic runs did not finish within the preview time limit of 90 seconds, so no run was"
             " created. The next hourly work evaluates again; raise the preview time limit if this repeats.")


def fake_proc(root, processes=(), boot=BOOT):
    """A fake /proc tree: this boot's id and, per (pid, start time), a stat line whose field 22 is the start time (the
    command name holds a space and a ')', as a real one may)."""
    root = Path(root)
    (root / "sys" / "kernel" / "random").mkdir(parents=True, exist_ok=True)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text(boot + "\n", encoding="utf-8", newline="\n")
    for pid, start in processes:
        fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 30
        (root / str(pid)).mkdir(parents=True, exist_ok=True)
        (root / str(pid) / "stat").write_text(f"{pid} (archive.sh) x) " + " ".join(fields) + "\n", encoding="utf-8",
                                               newline="\n")
    return root


def with_proc(source, target, proc):
    """A copy of SOURCE at TARGET whose PROC constant names the fake tree PROC."""
    text = Path(source).read_text(encoding="utf-8")
    assert text.count("\nPROC=/proc\n") == 1, source
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(text.replace("\nPROC=/proc\n", f"\nPROC={sh_path(proc)}\n"), encoding="utf-8",
                            newline="\n")
    return Path(target)


def epoch_minute(hour, minute):
    return calendar.timegm((2026, 10, 8, hour, minute, 0)) // 60


def job_doc(step, result, rc=0, run_id=None, state=None, error=None, request_id=None, view_id=None, **fields):
    """A `python -m app.cli history-archive <step>` report (contract B1 §2.3 key order; step 3 adds the common key
    request_id right after run_id, step 5 the common key view_id right after request_id)."""
    document = {"report_version": 1, "command": f"history-archive {step.split()[0]}", "result": result,
                "exit_code": rc, "started_at": "2026-10-08T12:20:00Z", "finished_at": "2026-10-08T12:20:01Z",
                "duration_ms": 1000, "run_id": run_id, "request_id": request_id, "view_id": view_id, "state": state,
                **fields, "error": None if error is None else {"code": error[0], "message": error[1]}}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def job_out(step, result, rc=0, run_id=None, state=None, error=None, request_id=None, view_id=None, **fields):
    run = f" run {run_id}" if run_id is not None else ""
    run += f" deletion request {request_id}" if request_id is not None else ""
    run += f" view {view_id}" if view_id is not None else ""
    summary = f"history-archive {step.split()[0]}:{run} {result}" + (f" ({state})" if state else "") + "\n"
    return out(job_doc(step, result, rc, run_id, state, error, request_id, view_id, **fields), rc=rc, stderr=summary)


def run_object(state, **fields):
    """The run summary of `next`, `evaluate` and `status` (contract §3; step 4 adds trigger, reason, approved_by_user_id,
    approved_at, authorization_id and authorization_failed): a requested (manual) run unless FIELDS say otherwise."""
    document = {"id": RUN, "state": state, "trigger": "MANUAL", "reason": "Quarterly archival",
                "cutoff": "2024-10-08T00:00:00Z", "requested_at": "2026-10-08T10:00:00Z", "approved_by_user_id": None,
                "approved_at": None, "authorization_id": None, "authorization_failed": False,
                "archive_name": None if state in ("PROPOSED", "APPROVED") else ARCHIVE_NAME, "manifest_sha256": None,
                "copied": False, "copy_confirmed": False, "skipped_components": None, "skipped_flows": None,
                "pre_purge_backup_name": None, "purged": None, "receipt_sha256": None, "finished_at": None,
                "result": None, "failure_reason": None}
    document.update(fields)
    return document


def automatic_run(state, **fields):
    """The summary of an automatic run created under the standing authorization of audit row 12 (contract B4 §3)."""
    return run_object(state, trigger="AUTOMATIC", reason=AUTOMATIC_REASON, authorization_id=12, **fields)


def next_out(action, state=None, rc=0, error=None, request=None, run=None, view=None):
    """A `history-archive next` answer: ACTION for run 3 in STATE (`none`: no open run); with `backup`, the pre-purge
    backup's kind and label; exit 3: the re-check of the run's authority failed the run. REQUEST: an APPROVED deletion
    request that waits while the run is RUNNING (step 3; `request_id` stays null). RUN: the run summary, when it is not
    a requested run's (step 4: an automatic run). VIEW: a view of an archive whose viewing work waits while the run is
    RUNNING (step 5; `view_id` stays null)."""
    if action == "none":
        return job_out("next", "none", run=None, request=request, view=view)
    fields = {"run": run or run_object(state), "request": request, "view": view}
    if action == "backup":
        fields["backup"] = {"kind": "pre-purge", "label": ARCHIVE_NAME}
    return job_out("next", "failed" if rc == 3 else action, rc=rc, run_id=RUN, state=state, error=error, **fields)


def purge_out(result="completed", rc=0, state="COMPLETED", resumed=False, started=True, error=None, skipped=(0, 0)):
    """A `history-archive purge` answer; STARTED: the run reached PURGING, so the report names its pre-purge backup."""
    fields = {"archive_name": ARCHIVE_NAME, "resumed": resumed}
    if started:
        fields["backup"] = {"name": BACKUP, "dump_started_at": DUMP_STARTED}
    if rc == 0:
        fields.update({"batches": 2, "purged": {"components": 118, "flows": 305, "movements": 4190},
                       "skipped": {"components": skipped[0], "flows": skipped[1]},
                       "receipt": {"name": f"{ARCHIVE_NAME}.purged.json", "sha256": "c" * 64, "copied_to": None,
                                   "copy_problem": None}})
    elif rc == 3:
        result = "failed"
    else:
        result = "could_not_run"
    return job_out("purge", result, rc=rc, run_id=RUN, state=state, error=error, **fields)


# The automatic runs of a `history-archive status` report (step 4): off, as initially.
AUTOMATIC_OFF = {"enabled": False, "approval_required": True, "standing_authorization": None}


def status_ok():
    return job_out("status", "ok", open_run=None, last_finished_run=None,
                   archive_dir={"path": "/archive", "usable": True, "free_bytes": 1, "total_bytes": 2, "partial": []},
                   copy_dir=None, receipts_not_copied=[], open_deletion_request=None,
                   last_finished_deletion_request=None, automatic_runs=AUTOMATIC_OFF, max_wait_days=7, findings=[])


def evaluate_out(result="off", rc=0, run_id=None, state=None, error=None, message=None, run=None, approval=True):
    """A `history-archive evaluate` answer (contract B4 §3.1): every decision exits 0 with its result and message; a
    created run (RUN, PROPOSED or APPROVED) or the open run of `run_open` is named by run_id and state."""
    if rc != 0:
        return job_out("evaluate", "refused" if rc == 1 else "could_not_run", rc=rc, error=error)
    enabled = result != "off"
    if message is None:
        message = CREATED_APPROVED if (result, state) == ("created", "APPROVED") else EVALUATE_MESSAGES[result]
    if run is None and result == "created":
        run = (automatic_run(state) if state == "APPROVED"
               else run_object(state, trigger="AUTOMATIC", reason=AUTOMATIC_REASON))
    measured = result in ("below_limit", "nothing_eligible", "settings_changed", "created")
    return job_out(
        "evaluate", result, run_id=run_id, state=state, automatic_runs=enabled, approval_required=approval,
        retention_period_months=24 if enabled else None,
        cutoff="2024-10-08T00:00:00.000000Z" if enabled else None, cutoff_date="2024-10-08" if enabled else None,
        interval_days=7, open_run={"id": run_id, "state": state} if result == "run_open" else None,
        last_automatic_run=None, next_due_at=None, standing_authorization=None,
        measure=({"name": "LIVE_MOVEMENT_ROWS", "read": "count", "value": 5120334, "limit": 5000000, "threshold": None,
                  "passes": True} if measured else None),
        preview=None, message=message, run=run)


def backup_line(name=BACKUP, started=DUMP_STARTED):
    return out(f"BACKUP {name} /backups/{name} {started}\n", stderr="backup: verify ok (10 ms)\n")


HAPPY = (("export", "APPROVED"), ("verify", "RUNNING"), ("copy", "RUNNING"), ("backup", "RUNNING"))


def request_object(state, request_id=REQ, **fields):
    """The deletion request summary of `settings`, `next`, `start-deletion`, `delete` and `status` (contract B3 §3)."""
    started = state not in ("APPROVED", "CANCELLED")
    document = {"id": request_id, "state": state,
                "scope": {"work_order_numbers": ["WO-TEST-1"], "part_numbers": []},
                "requested_at": "2026-10-08T12:17:00Z", "requested_by_user_id": 1, "requested_by_operator": None,
                "rows": {"audit_events": 4, "part_movements": 12, "quantity_flow_lineage": 0, "quantity_flows": 2,
                         "assigned_route_steps": 6, "assigned_routes": 2, "work_order_allocations": 0,
                         "work_order_demands": 1, "work_orders": 1, "part_numbers": 0},
                "started_at": "2026-10-08T12:20:31Z" if started else None,
                "backup_label": DEL_LABEL if started else None,
                "pre_purge_backup_name": DEL_BACKUP if state == "COMPLETED" else None,
                "finished_at": "2026-10-08T12:20:40Z" if state in ("COMPLETED", "FAILED", "CANCELLED") else None,
                "failure_reason": None}
    document.update(fields)
    return document


def settings_out(pending=False, request_state="APPROVED", start_minute=20, request=None, views=()):
    """The cheap check: `history-archive settings` through `exec` in the backend container (contract B3 §3); REQUEST:
    the open request's summary (pending). VIEWS (step 5, contract B5 §3.1): the views with work for the job, as
    (view id, state, action) in the order of `pending_views`."""
    if request is not None:
        pending = True
    elif pending:
        request = request_object(request_state)
    return job_out("settings", "read", start_minute=start_minute, archive_dir=None, copy_dir=None,
                   site_timezone="UTC", deletion_pending=pending, deletion_request=request,
                   viewing_pending=bool(views),
                   pending_views=[{**view_object(state, view_id), "action": action} for view_id, state, action in views])


def next_deletion_out(action, state):
    """A `next` answer that hands out deletion work for request 7 (`run` null)."""
    fields = {"run": None, "request": request_object(state)}
    if action == "deletion_backup":
        fields["backup"] = {"kind": "pre-purge", "label": DEL_LABEL}
    return job_out("next", action, request_id=REQ, state=state, **fields)


def start_deletion_out(result="started", rc=0, error=None):
    state = "RUNNING" if rc == 0 else ("FAILED" if rc == 3 else "APPROVED")
    fields = {"request": request_object(state)}
    if rc == 0:
        fields["backup"] = {"kind": "pre-purge", "label": DEL_LABEL}
    return job_out("start-deletion", result, rc=rc, request_id=REQ, state=state, error=error, **fields)


def delete_out(result="deleted", rc=0, state="COMPLETED", error=None):
    fields = {"backup": {"name": DEL_BACKUP, "dump_started_at": DEL_DUMP}}
    if rc == 0:
        fields.update({"request": request_object("COMPLETED"),
                       "deleted": {"audit_events": 4, "part_movements": 12, "quantity_flow_lineage": 0,
                                   "quantity_flows": 2, "assigned_route_steps": 6, "assigned_routes": 2,
                                   "work_order_allocations": 0, "work_order_demands": 1, "work_orders": 1,
                                   "part_numbers": 0},
                       "audit_rows": [{"id": 90, "entity_type": "WorkOrder", "entity_id": "41"}]})
    return job_out("delete", result, rc=rc, request_id=REQ, state=state, error=error, **fields)


def deletion_status(open_request=None, last_request=None, rc=0):
    """`history-archive status` with the deletion keys of step 3."""
    return job_out("status", "ok", rc=rc, open_run=None, last_finished_run=None,
                   archive_dir={"path": "/archive", "usable": True, "free_bytes": 1, "total_bytes": 2, "partial": []},
                   copy_dir=None, receipts_not_copied=[], open_deletion_request=open_request,
                   last_finished_deletion_request=last_request, automatic_runs=AUTOMATIC_OFF, max_wait_days=7,
                   findings=[])


# Step 5, viewing an archive (contract B5): view 4 of run 2 waits to be loaded; view 3 of run 2, closed by a person,
# waits for the job to remove its rows.
VIEW = 4
CLOSED_VIEW = 3
VIEW_RUN = 2
VIEW_ARCHIVE = "run-2-20261001T122000Z"
LOADED = {"movements": 4190, "flows": 305, "lineage_edges": 12, "assigned_routes": 40, "names": 97}
REMOVED = {"archive_view_names": 97, "archive_view_lineage": 12, "archive_view_flows": 305, "archive_view_routes": 40,
           "archive_view_movements": 4190}
VIEW_AGAIN = "Open the run for viewing again in Administration → History archival & purge (OPERATIONS_RUNBOOK §11)."


def view_object(state, view_id=VIEW, **fields):
    """The view summary of `settings`, `next`, `load-view`, `close-view` and `status` (contract B5 §3): view VIEW_ID of
    run 2, requested by user 1; loaded and open for 7 days once it opened, and closed by user 1 once it is closing."""
    opened = state in ("OPEN", "CLOSING", "CLOSED")
    closing = state in ("CLOSING", "CLOSED")
    document = {"id": view_id, "run_id": VIEW_RUN, "state": state, "requested_at": "2026-10-08T12:17:00.000000Z",
                "requested_by_user_id": 1,
                "started_at": None if state in ("REQUESTED", "CANCELLED") else "2026-10-08T12:20:31.000000Z",
                "opened_at": "2026-10-08T12:21:03.000000Z" if opened else None,
                "open_until": "2026-10-15T12:21:03.000000Z" if opened else None,
                "loaded": dict(LOADED) if opened else None,
                "closing_started_at": "2026-10-08T12:30:00.000000Z" if closing else None,
                "closed_by_user_id": 1 if closing else None,
                "finished_at": "2026-10-08T12:30:05.000000Z" if state in ("CLOSED", "FAILED", "CANCELLED") else None,
                "failure_reason": None}
    document.update(fields)
    return document


def next_view_out(action, state, view_id=VIEW):
    """A `next` answer that hands out viewing work, `close_view` or `load_view`, for VIEW_ID in STATE (`run` and
    `request` null; `view_id` and `state` name the view)."""
    return job_out("next", action, view_id=view_id, state=state, run=None, request=None,
                   view=view_object(state, view_id))


def load_view_out(result="loaded", rc=0, view_id=VIEW, error=None, state=None):
    """A `history-archive load-view` answer (contract B5 §3.3): `loaded` (OPEN, with what it loaded), `refused` (the
    view untouched: no state, no view), `interrupted` or `could_not_run` (LOADING by default), `failed` (FAILED, its
    rows removed)."""
    if state is None:
        state = {"loaded": "OPEN", "refused": None, "interrupted": "LOADING", "could_not_run": "LOADING",
                 "failed": "FAILED"}[result]
    fields = {}
    if state is not None:
        fields = {"view": view_object(state, view_id, failure_reason=error[1] if state == "FAILED" else None),
                  "archive_name": VIEW_ARCHIVE}
    if result == "loaded":
        fields["loaded"] = dict(LOADED)
    return job_out("load-view", result, rc=rc, view_id=view_id, state=state, error=error, **fields)


def close_view_out(result="closed", rc=0, view_id=CLOSED_VIEW, error=None, state=None, by_person=True):
    """A `history-archive close-view` answer (contract B5 §3.4): `closed` (CLOSED, with the rows it removed per viewer
    table), `refused` (no state, no view), `interrupted` or `could_not_run` (CLOSING by default). BY_PERSON: a person
    closed the view (closed_by_user_id), otherwise its days ran out."""
    if state is None:
        state = {"closed": "CLOSED", "refused": None, "interrupted": "CLOSING", "could_not_run": "CLOSING"}[result]
    fields = {}
    if state is not None:
        fields["view"] = view_object(state, view_id, closed_by_user_id=1 if by_person else None)
    if result == "closed":
        fields["removed"] = dict(REMOVED)
    return job_out("close-view", result, rc=rc, view_id=view_id, state=state, error=error, **fields)


def recover_out(**lists):
    """A `history-archive recover` answer with the lists of contract B5 §3.5 (`failed_view_ids` added by step 5)."""
    fields = {"failed_run_ids": [], "failed_request_ids": [], "failed_view_ids": [], "removed_partial": [],
              "kept_partial": [], "copied_receipts": [], "receipt_problems": [], **lists}
    changed = any(fields[key] for key in ("failed_run_ids", "failed_request_ids", "failed_view_ids", "removed_partial",
                                          "copied_receipts"))
    return job_out("recover", "recovered" if changed else "nothing_to_recover", **fields)


def default_jobs(*nexts):
    """One approved run in one start (contract §3.1): export, verify, copy into the copy folder, the pre-purge backup,
    the purge and the reconcile after it. NEXTS: the successive answers of `next`, as (action, state)."""
    return [
        rule(CHEAP, settings_out()),
        rule(MAINT + "settings$", job_out("settings", "read", start_minute=20, archive_dir="/archive", copy_dir=None,
                                          site_timezone="UTC")),
        rule(MAINT + "recover$", job_out("recover", "nothing_to_recover", failed_run_ids=[], removed_partial=[],
                                         kept_partial=[], copied_receipts=[], receipt_problems=[])),
        # Step 4: the automatic runs are off, as initially.
        rule(MAINT + "evaluate$", evaluate_out()),
        rule(MAINT + "next$", *[next_out(*answer) for answer in (nexts or HAPPY)]),
        rule(MAINT + f"export --run {RUN}$", job_out("export", "exported", run_id=RUN, state="RUNNING",
                                                     archive_name=ARCHIVE_NAME, manifest_sha256="a" * 64,
                                                     archive_bytes=10, counts={"components": 1, "flows": 2,
                                                                               "movements": 3, "lineage_edges": 0,
                                                                               "assigned_routes": 0})),
        rule(MAINT + f"verify --run {RUN}$", job_out("verify", "verified", run_id=RUN, state="RUNNING",
                                                     archive_name=ARCHIVE_NAME, manifest_sha256="a" * 64,
                                                     skipped_components=0, skipped_flows=0)),
        rule(MAINT + f"copy --run {RUN}$", job_out("copy", "copied", run_id=RUN, state="RUNNING",
                                                   archive_name=ARCHIVE_NAME,
                                                   copied_to=f"/archive-copy/{ARCHIVE_NAME}", copy_problem=None)),
        rule(PURGE + "( |$)", purge_out()),
        rule(RECONCILE, out(reconcile_doc(0))),
        rule(MAINT + "status$", status_ok()),
        rule(MAINTENANCE_PS, out("")),
    ]


class ArchiveHarness(ScriptHarness):
    tools = ScriptHarness.tools + ("date",)

    def setUp(self):
        super().setUp()
        self.archive_dir = self.tmp / "archive"
        self.archive_dir.mkdir()
        self.copy_dir = self.tmp / "archive-copy"
        self.env_lines.append(f"PARTFLOW_ARCHIVE_DIR={sh_path(self.archive_dir)}")
        self.write_env(self.env_lines)
        self.lock = self.records / ".archive.lock"
        self.stamp = self.records / "archive" / "hourly-slot"
        self.incident = self.records / "archive" / "reconcile-after-purge"
        self.rules["docker"] = default_jobs()
        self.set_clock(BASE + 20 * 60 + 30)
        self.watch([self.lock / "owner"])
        # archive.sh runs from a copy whose PROC names a fake /proc (this boot BOOT; pid 4242 started at tick 777),
        # beside a stub backup.sh that prints the BACKUP line of a pre-purge backup.
        self.proc = fake_proc(self.tmp / "proc", [(4242, 777)])
        self.archive_sh = with_proc(ARCHIVE_SH, self.root / "deploy" / "production" / "archive.sh", self.proc)
        state = self.state.as_posix()
        # The stub sends an answer's signal to its caller (archive.sh), as the fake tools do.
        self.wrapper(self.archive_sh.parent / "backup.sh",
                     f'"{self.python}" "{self.fake.as_posix()}" "{state}" backup.sh "$@"\nrc=$?\n'
                     f'if [ -f "{state}/signal" ]; then\n'
                     f'    signal=$(cat "{state}/signal"); rm -f "{state}/signal"; kill -s "$signal" "$PPID"\n'
                     f'fi\nexit $rc')
        self.rules["backup.sh"] = [rule(r".*", backup_line())]

    def set_clock(self, epoch):
        moment = time.gmtime(epoch)
        self.rules["date"] = [
            rule(r"^\+%s %M %S$", out(f"{epoch} {moment.tm_min:02d} {moment.tm_sec:02d}\n")),
            rule(r"^-u \+%Y-%m-%dT%H:%M:%SZ$", out(time.strftime("%Y-%m-%dT%H:%M:%SZ", moment) + "\n")),
            rule(r"^-u \+%Y%m%dT%H%M%SZ$", out(time.strftime("%Y%m%dT%H%M%SZ", moment) + "\n")),
        ]

    def at(self, hour, minute, second=30):
        self.set_clock(calendar.timegm((2026, 10, 8, hour, minute, second)))

    def archive(self, *arguments):
        return self.run_script(self.archive_sh, "--records-dir", self.records.as_posix(), *arguments)

    def steps(self):
        """The history-archive steps in order (`maintenance <step>` of each docker call)."""
        found = []
        for label in self.docker():
            match = re.search(r" maintenance (.*)$", label)
            if match:
                found.append(match.group(1))
        return found

    def order(self):
        """The hourly work in order across the fakes: the maintenance steps, backup.sh and the reconcile."""
        found = []
        for entry in self.calls():
            text = " ".join(entry["argv"])
            if entry["tool"] == "backup.sh":
                found.append("backup.sh")
            elif entry["tool"] == "docker":
                match = re.search(r" maintenance (.*)$", text)
                if match:
                    found.append(match.group(1))
                elif re.search(RECONCILE, text):
                    found.append("reconcile")
        return found

    def backup_calls(self):
        return [entry["argv"] for entry in self.calls("backup.sh")]

    def maintenance_calls(self):
        return [label for label in self.docker() if re.search(r" maintenance ", label)]

    def set_job(self, step, *answers):
        pattern = MAINT + re.escape(step) + "$"
        self.set_rule(pattern, *answers)

    def set_rule(self, pattern, *answers):
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != pattern]
        self.rules["docker"].insert(0, rule(pattern, *answers))

    def set_next(self, *answers):
        self.set_rule(MAINT + "next$", *[next_out(*answer) for answer in answers])

    def set_purge(self, answer):
        self.set_rule(PURGE + "( |$)", answer)

    def set_deletion(self, *nexts):
        """An open deletion request 7: the cheap check finds it, `next` hands out its start and then its pre-purge
        backup (a run waiting behind it is never asked for), start-deletion starts it, backup.sh takes its backup and
        delete deletes it."""
        self.set_rule(CHEAP, settings_out(pending=True))
        answers = [next_deletion_out(*answer) for answer in (nexts or (("start_deletion", "APPROVED"),
                                                                      ("deletion_backup", "RUNNING")))]
        self.set_rule(MAINT + "next$", *answers, next_out("export", "APPROVED"))
        self.set_job(f"start-deletion --request {REQ}", start_deletion_out())
        self.set_rule(MAINT + f"delete --request {REQ} .*$", delete_out())
        self.rules["backup.sh"] = [rule(r".*", backup_line(name=DEL_BACKUP, started=DEL_DUMP))]

    def write_stamp(self, value):
        self.stamp.parent.mkdir(parents=True, exist_ok=True)
        self.stamp.write_text(f"{value}\n", encoding="utf-8", newline="\n")

    def stamp_value(self):
        return int(self.stamp.read_text(encoding="utf-8").strip()) if self.stamp.exists() else None

    def record_dirs(self):
        root = self.records / "archive"
        return sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []

    def incident_values(self):
        lines = self.incident.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    @staticmethod
    def write_incident(path):
        """An incident record of archive.sh (the reconcile after the purge of run 3 found a mismatch in check g)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"result=mismatch\nexit_code=1\nrun={RUN}\narchive_name={ARCHIVE_NAME}\n"
                        f"pre_purge_backup={BACKUP}\nfailing_checks=g\nfinished_at=2026-10-08T11:20:40Z\n"
                        "report=/records/archive/20261008T112030Z/reconcile.json\n", encoding="utf-8", newline="\n")

    def reset_calls(self):
        calls = self.state / "calls.jsonl"
        if calls.exists():
            calls.unlink()

    def cheap_only(self):
        """The start ran the cheap check and no other Docker command."""
        labels = self.docker()
        return len(labels) == 1 and re.search(CHEAP, " " + labels[0]) is not None

    def write_lock(self, host="bs-host", pid="4242", owner=True, boot=None, start=None):
        self.lock.mkdir(parents=True)
        if owner:
            text = f"host={host}\npid={pid}\nstarted_at=2026-10-08T11:20:00Z\nby=archive.sh\n"
            text += "" if boot is None else f"boot={boot}\n"
            text += "" if start is None else f"start={start}\n"
            (self.lock / "owner").write_text(text, encoding="utf-8", newline="\n")

    def lock_during(self, step):
        for entry in self.calls("docker"):
            if " ".join(entry["argv"]).endswith(f"maintenance {step}"):
                return entry["watch"][str(self.lock / "owner")]
        self.fail(f"no maintenance {step} call")

    def lock_at(self, tool, pattern=r""):
        for entry in self.calls(tool):
            if re.search(pattern, " ".join(entry["argv"])):
                return entry["watch"][str(self.lock / "owner")]
        self.fail(f"no {tool} call matching {pattern}")


# ---------------------------------------------------------------------------
# AR-1..AR-4: the hourly work
# ---------------------------------------------------------------------------


class HourlyWork(ArchiveHarness):
    # AR-1: an approved run is exported, verified, copied, backed up and purged in one start, the full reconcile runs
    # after the purge and status at the end (contract §3.1); the lock is held throughout.
    def test_ar1_one_start_from_export_to_purge(self):
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "next", f"copy --run {RUN}", "next", "backup.sh",
                                        PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertEqual(self.backup_calls(), [BACKUP_ARGUMENTS + ["60"]])
        for label in self.maintenance_calls():
            self.assertTrue(label.startswith("--profile ops run --rm --no-deps -T --user 1000:1000 maintenance "), label)
        reconcile = self.docker_entries(RECONCILE)
        self.assertEqual(len(reconcile), 1)
        self.assertEqual(reconcile[0]["argv"][:5], ["compose", "-f", "compose.production.yaml", "--env-file",
                                                    ".env.production"])
        owner = self.lock_during("recover")
        self.assertIsNotNone(owner)
        self.assertIn("by=archive.sh\n", owner)
        self.assertIn(f"boot={BOOT}\n", owner)
        self.assertRegex(owner, r"(?m)^start=[0-9]*$")
        self.assertIn("host=bs-host\n", owner)
        # The archive lock is held for the backup, the purge and the reconcile too (release.sh refuses meanwhile).
        for held in (self.lock_at("backup.sh"), self.lock_at("docker", r" maintenance purge "),
                     self.lock_at("docker", RECONCILE)):
            self.assertEqual(held, owner)
        self.assertFalse(self.lock.exists())
        records = self.record_dirs()
        self.assertEqual(len(records), 1)
        for name in ("recover.json", "evaluate.json", "next.json", "next-2.json", "next-3.json", "next-4.json",
                     "export.json", "verify.json", "copy.json", "backup.out", "backup.log", "purge.json",
                     "reconcile.json", "reconcile.log", "status.json", "messages.log"):
            self.assertTrue((records[0] / name).is_file(), name)
        self.assertIn("history-archive copy: run 3 copied (RUNNING)", self.result.stderr)
        self.assertIn(f"run 3: the pre-purge backup {BACKUP} is published and verified (its dump started at"
                      f" {DUMP_STARTED}).", self.result.stderr)
        self.assertIn("run 3: 118 components (305 flows, 4190 Movements) purged, 0 components (0 flows) skipped",
                      self.result.stderr)
        self.assertIn(f"purge receipt {sh_path(self.archive_dir)}/{ARCHIVE_NAME}.purged.json", self.result.stderr)
        self.assertIn("the reconcile after the purge of run 3 is clean", self.result.stderr)
        self.assertFalse(self.incident.exists())
        # A manual start never writes the hourly stamp and never reads the settings.
        self.assertIsNone(self.stamp_value())
        self.assertNotIn("settings", self.steps())

    # AR-2: nothing eligible ends the run after the export; every component changed after the export ends it after the
    # verification (COMPLETED_WITH_SKIPS, NOTHING_PURGED): no copy, no backup, no purge.
    def test_ar2_runs_that_end_before_the_purge(self):
        self.set_next(("export", "APPROVED"))
        self.set_job(f"export --run {RUN}", job_out("export", "nothing_eligible", run_id=RUN, state="NOTHING_ELIGIBLE",
                                                    counts={"components": 0}))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE nothing_eligible {RUN} NOTHING_ELIGIBLE\n")
        self.reset()
        self.set_next(("export", "APPROVED"), ("verify", "RUNNING"))
        self.set_job(f"verify --run {RUN}", job_out(
            "verify", "nothing_to_purge", run_id=RUN, state="COMPLETED_WITH_SKIPS", archive_name=ARCHIVE_NAME,
            manifest_sha256="a" * 64, skipped_components=1, skipped_flows=2,
            receipt={"name": f"{ARCHIVE_NAME}.purged.json", "sha256": "d" * 64, "copied_to": None, "copy_problem": None}))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE nothing_to_purge {RUN} COMPLETED_WITH_SKIPS\n")
        self.assertIn("nothing is left to purge", self.result.stderr)
        self.assertEqual(self.backup_calls(), [])

    # AR-3: nothing open, or a run that waits for its confirmation: no step for a run, no record kept. The hourly work
    # worked on no run, so its final line is the decision of evaluate (step 4; the automatic runs are off) with the open
    # run that `next` named.
    def test_ar3_nothing_to_do(self):
        for answer, stdout in ((("none",), "EVALUATE off - -\n"),
                               (("wait", "WAITING_FOR_COPY"), f"EVALUATE off {RUN} WAITING_FOR_COPY\n")):
            with self.subTest(answer=answer):
                self.reset()
                self.set_next(answer)
                self.archive()
                self.assertExit(0)
                self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
                self.assertEqual(self.result.stdout, stdout)
                self.assertEqual(self.record_dirs(), [])
                self.assertFalse(self.lock.exists())

    # AR-4: a confirmed (or copyable) waiting run continues with the backup and the purge in the same start; a copy that
    # still waits keeps no record.
    def test_ar4_copy_of_a_waiting_run(self):
        self.set_next(("copy", "WAITING_FOR_COPY"), ("backup", "RUNNING"))
        self.set_job(f"copy --run {RUN}", job_out("copy", "confirmed", run_id=RUN, state="RUNNING",
                                                  archive_name=ARCHIVE_NAME, copied_to=None, copy_problem=None))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"copy --run {RUN}", "next", "backup.sh",
                                        PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertEqual(len(self.record_dirs()), 1)
        self.reset()
        self.set_next(("copy", "WAITING_FOR_COPY"))
        self.set_job(f"copy --run {RUN}", job_out("copy", "waiting_for_copy", run_id=RUN, state="WAITING_FOR_COPY",
                                                  archive_name=ARCHIVE_NAME, copied_to=None,
                                                  copy_problem="The copy folder /archive-copy does not exist or cannot be written."))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.result.stdout, f"ARCHIVE waiting_for_copy {RUN} WAITING_FOR_COPY\n")
        self.assertIn("The copy folder /archive-copy does not exist or cannot be written.", self.result.stderr)
        self.assertIn("confirm it in Administration", self.result.stderr)
        self.assertEqual(self.record_dirs(), [])
        self.assertEqual(self.backup_calls(), [])


# ---------------------------------------------------------------------------
# AR-5..AR-9: exit codes
# ---------------------------------------------------------------------------


class ExitCodes(ArchiveHarness):
    # AR-5: exit 3 ends the run as FAILED; nothing after the failed step but status.
    def test_ar5_failed_run(self):
        reason = "The archive changed between export and verification. Nothing was deleted; request a new run."
        self.set_job(f"verify --run {RUN}", job_out("verify", "failed", rc=3, run_id=RUN, state="FAILED",
                                                    error=("run_failed", reason)))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, "")
        self.assertIn(f"archive: run {RUN} FAILED: {reason} Nothing was deleted. {REQUEST_NEW}", self.result.stderr)
        self.assertEqual(len(self.record_dirs()), 1)
        self.assertFalse(self.lock.exists())

    # AR-6: a refusal (another history-archive command holds the job's database lock) changes nothing: exit 1, and
    # without a recover no run is worked on.
    def test_ar6_refused_step(self):
        self.set_job("recover", job_out("recover", "refused", rc=1,
                                        error=("job_running", "Another history-archive command is running. Nothing was changed.")))
        self.archive()
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertIn("history-archive recover refused: Another history-archive command is running.", self.result.stderr)

    # AR-7: a report without its exit-code line (or none at all) is "could not run", the outcome unknown: exit 2.
    def test_ar7_invalid_report(self):
        self.set_job(f"export --run {RUN}", out("", rc=2, stderr="Error response from daemon: oops\n"))
        self.archive()
        self.assertExit(2)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "status"])
        self.assertIn("history-archive export gave no valid report (exit 2;", self.result.stderr)
        self.assertIn("the outcome of export is unknown. The next start recovers a run left RUNNING", self.result.stderr)
        self.reset()
        self.set_job("next", out(job_doc("next", "none"), rc=1))
        self.archive()
        self.assertExit(2)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
        self.reset()
        self.set_job("recover", out("", rc=2, stderr="could not reach the database\n"))
        self.archive()
        self.assertExit(2)
        self.assertEqual(self.order(), ["recover", "status"])

    # AR-8: recover found a run left RUNNING by an earlier start (or by a failed pre-purge backup, or restored from its
    # pre-purge backup): it is FAILED (exit 3); the work still continues.
    def test_ar8_recovered_stale_run(self):
        self.set_job("recover", job_out("recover", "recovered", failed_run_ids=[2],
                                        removed_partial=["/archive/.partial/run-2-20261008T112000Z"],
                                        kept_partial=["/archive/.partial/unknown"], copied_receipts=[],
                                        receipt_problems=[]))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.order()[-4:], ["backup.sh", PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertIn("run(s) 2 stopped while RUNNING and are FAILED now (an interrupted job, a pre-purge backup that"
                      " failed, or a database restored from a pre-purge backup). Nothing was deleted in this database.",
                      self.result.stderr)
        self.assertIn("kept the unfinished folder /archive/.partial/unknown", self.result.stderr)

    # AR-9: preflight refusals run nothing; status findings never change the exit status.
    def test_ar9_preflight_and_findings(self):
        for lines, message in (
            ([line for line in self.env_lines if not line.startswith("PARTFLOW_ARCHIVE_DIR=")],
             "PARTFLOW_ARCHIVE_DIR in .env.production is empty"),
            ([*self.env_lines, "PARTFLOW_ARCHIVE_DIR=relative"], "PARTFLOW_ARCHIVE_DIR in .env.production must be an absolute path"),
            ([*self.env_lines, f"PARTFLOW_ARCHIVE_DIR={sh_path(self.tmp)}/missing"], "does not exist"),
        ):
            with self.subTest(message=message):
                self.write_env(lines)
                self.archive()
                self.assertExit(2)
                self.assertIn(message, self.result.stderr)
                self.assertEqual(self.calls("docker"), [])
        self.reset()
        self.set_job("status", job_out("status", "finding", rc=1, findings=["copy_confirmation_overdue",
                                                                           "receipt_not_copied"]))
        self.archive()
        self.assertExit(0)
        self.assertIn("status findings: copy_confirmation_overdue receipt_not_copied", self.result.stderr)

    def test_usage(self):
        result = self.archive("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: deploy/production/archive.sh", result.stdout)
        self.assertIn("--backup-wait-minutes N", result.stdout)
        self.assertIn("0-1440, default 60", result.stdout)
        for arguments, message in ((("--bogus",), "unknown argument --bogus."),
                                   (("--scheduled", "--scheduled"), "--scheduled is given twice."),
                                   (("--env-file",), "--env-file needs a value."),
                                   (("--backup-wait-minutes", "1441"),
                                    "--backup-wait-minutes must be a whole number from 0 to 1440."),
                                   (("--backup-wait-minutes", "x"),
                                    "--backup-wait-minutes must be a whole number from 0 to 1440.")):
            with self.subTest(arguments=arguments):
                self.archive(*arguments)
                self.assertExit(2)
                self.assertIn(message, self.result.stderr)
                self.assertEqual(self.calls(), [])

    def test_posix_sh(self):
        text = ARCHIVE_SH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("\nset -eu\n", text)
        self.assertNotIn("\r", text)
        self.assertIsNotNone(find_sh(), SH_REQUIRED)


# ---------------------------------------------------------------------------
# AR-10..AR-12: the optional copy folder
# ---------------------------------------------------------------------------


# The fake docker keeps the generated override file of the call it answers (it is removed when archive.sh ends).
KEEP_OVERRIDE = """
import shutil
for i, a in enumerate(args):
    if a == "-f" and args[i + 1].endswith("copy-mount.yaml"):
        shutil.copyfile(args[i + 1], os.path.join(sys.argv[1], "override.yaml"))
"""
OVERRIDE = r" -f \S*copy-mount\.yaml --env-file "


class CopyFolder(ArchiveHarness):
    def configure_copy(self, create=True, mount="/mnt/usb"):
        if create:
            self.copy_dir.mkdir()
        self.write_env([*self.env_lines, f"PARTFLOW_ARCHIVE_COPY_DIR={sh_path(self.copy_dir)}"])
        self.prepend("df", rule(r"^-P .*archive-copy$", out(df_text(1, 2, mount))),
                     rule(r"^-P .*archive$", out(df_text(1, 2, "/volume1"))))

    def copy_calls(self):
        """Each maintenance call with True when it used the copy-folder override file (and never `-v`)."""
        found = []
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            match = re.search(r" maintenance (.*)$", text)
            if match:
                self.assertNotIn(" -v ", text)
                found.append((match.group(1), bool(re.search(OVERRIDE, text))))
        return found

    # AR-10: a usable copy folder on another device is mounted at /archive-copy for every step (the purge included: it
    # copies the purge receipt there), by a long-syntax bind that never creates a missing source.
    def test_ar10_mounted(self):
        self.configure_copy()
        step = MAINT + "recover$"
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != step]
        self.prepend("docker", rule(step, {**job_out("recover", "nothing_to_recover", failed_run_ids=[],
                                                     removed_partial=[], kept_partial=[], copied_receipts=[],
                                                     receipt_problems=[]), "run": KEEP_OVERRIDE}))
        self.archive()
        self.assertExit(0)
        calls = self.copy_calls()
        self.assertEqual(len(calls), 11)
        self.assertTrue(all(used for _, used in calls), calls)
        self.assertEqual(calls[1], ("evaluate", True))
        self.assertEqual(calls[9], (PURGE_WITH_BACKUP, True))
        override = (self.state / "override.yaml").read_text(encoding="utf-8")
        self.assertIn("services:\n    maintenance:\n        volumes:\n            - type: bind\n", override)
        self.assertRegex(override, r"source: '\S*archive-copy'\n")
        self.assertIn("target: /archive-copy\n", override)
        self.assertIn("create_host_path: false\n", override)
        # The reconcile runs in backend, never with the copy folder.
        self.assertNotIn("copy-mount.yaml", " ".join(self.docker_entries(RECONCILE)[0]["argv"]))

    # AR-11: a copy folder on the archive folder's file system, or on the root file system `/` (where the path of a
    # disk or share that is not mounted lies), is never used.
    def test_ar11_not_another_device(self):
        for mount, message in (
            ("/volume1", "is on the same file system as the archive folder (/volume1), so it is no off-host copy"),
            ("/", "is on the host's root file system (/), so it is no off-host copy (is the share or disk mounted?)"),
        ):
            with self.subTest(mount=mount):
                self.reset()
                self.configure_copy(mount=mount)
                self.archive()
                self.assertExit(0)
                self.assertFalse(any(used for _, used in self.copy_calls()))
                self.assertIn(message, self.result.stderr)
                self.assertEqual(self.result.stderr.count(message), 1, self.result.stderr)

    # AR-12: a missing copy folder is never created: the job finds it unusable and the run waits.
    def test_ar12_missing(self):
        self.configure_copy(create=False)
        self.archive()
        self.assertExit(0)
        self.assertFalse(any(used for _, used in self.copy_calls()))
        self.assertIn("does not exist (is the share or disk mounted?)", self.result.stderr)
        self.assertFalse(self.copy_dir.exists())

    # AR-12b: the rule is evaluated again before every step: a disk that drops out during the work (its path now on
    # `/`) is not used by the copy, and the run waits.
    def test_ar12b_dropped_out_before_the_copy(self):
        self.configure_copy()
        usb = out(df_text(1, 2, "/mnt/usb"))
        self.prepend("df", rule(r"^-P .*archive-copy$", usb, usb, usb, usb, usb, usb, usb, out(df_text(1, 2, "/"))))
        self.set_job(f"copy --run {RUN}", job_out("copy", "waiting_for_copy", run_id=RUN, state="WAITING_FOR_COPY",
                                                  archive_name=ARCHIVE_NAME, copied_to=None,
                                                  copy_problem="The copy folder /archive-copy does not exist or cannot be written."))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.copy_calls(), [("recover", True), ("evaluate", True), ("next", True),
                                             (f"export --run {RUN}", True), ("next", True), (f"verify --run {RUN}", True),
                                             ("next", True), (f"copy --run {RUN}", False), ("status", False)])
        self.assertIn("is on the host's root file system (/)", self.result.stderr)
        self.assertEqual(self.result.stdout, f"ARCHIVE waiting_for_copy {RUN} WAITING_FOR_COPY\n")


# ---------------------------------------------------------------------------
# AR-13..AR-17: --scheduled
# ---------------------------------------------------------------------------


class Scheduled(ArchiveHarness):
    # AR-13: the first scheduled start only records the current slot; the work starts at the next Start minute.
    def test_ar13_first_start_records_the_slot(self):
        self.at(12, 5)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        # The cheap check gives the Start minute too: no maintenance container, no lock.
        self.assertEqual(self.steps(), [])
        self.assertEqual(len(self.docker_entries(CHEAP)), 1)
        self.assertIsNone(self.docker_entries(CHEAP)[0]["watch"][str(self.lock / "owner")])
        self.assertEqual(self.stamp_value(), epoch_minute(11, 20))
        self.assertFalse(self.lock.exists())
        # 12:15: within the hour of the 11:20 slot: the cheap check only, no maintenance container.
        self.reset_calls()
        self.at(12, 15)
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertEqual(self.cheap_only(), True)
        # 12:20: the first start at or after the Start minute does the hourly work, the evaluation of the automatic runs
        # first.
        self.reset_calls()
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "next", f"copy --run {RUN}", "next", "backup.sh",
                                        PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        # 12:25 and 13:15: done for this hour.
        for hour, minute in ((12, 25), (13, 15)):
            self.reset_calls()
            self.at(hour, minute)
            self.archive("--scheduled")
            self.assertEqual(self.result.stdout, "NOT_DUE\n")
            self.assertTrue(self.cheap_only())

    # AR-14: a late Start minute (58) is served by the first start of the next hour; a changed minute applies at once.
    def test_ar14_start_minute_from_the_settings(self):
        self.set_rule(CHEAP, settings_out(start_minute=58))
        self.write_stamp(epoch_minute(11, 58))
        self.at(12, 55)
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertTrue(self.cheap_only())
        self.at(13, 0)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertIn("recover", self.steps())
        self.assertNotIn("settings", self.steps())
        self.assertEqual(self.stamp_value(), epoch_minute(12, 58))
        # The Start minute moved from 20 to 50 after the 12:20 slot: the 12:50 slot is due at the 13:20 start.
        self.reset()
        self.set_rule(CHEAP, settings_out(start_minute=50))
        self.write_stamp(epoch_minute(12, 20))
        self.at(13, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 50))
        self.assertIn("recover", self.steps())
        # Without an answer of the cheap check (the backend is down), the maintenance service reads the Start minute.
        self.reset()
        self.set_rule(CHEAP, out("service \"backend\" is not running\n", rc=1))
        self.prepend("docker", rule(MAINT + "settings$", job_out("settings", "read", start_minute=50)))
        self.write_stamp(epoch_minute(12, 20))
        self.at(13, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.steps()[:2], ["settings", "recover"])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 50))
        # The hourly work's `next` still hands out an open deletion request and viewing work first (step 5).
        self.assertIn("the cheap check (history-archive settings in the running backend container) gave no answer"
                      " (exit 1): the hourly work goes on, and its next step still hands out an open deletion request"
                      " and viewing work first.", self.result.stderr)

    # AR-15: a missed slot (the host was down at 11:20) is caught up once by the next start; 12:20 runs as usual.
    def test_ar15_missed_slot(self):
        self.write_stamp(epoch_minute(10, 20))
        self.at(12, 10)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertIn("recover", self.steps())
        self.assertEqual(self.stamp_value(), epoch_minute(11, 20))
        self.reset_calls()
        self.at(12, 15)
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertTrue(self.cheap_only())
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertIn("recover", self.steps())
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))

    # AR-16: a failure is reported once per hour: the stamp is written, the next start within the hour runs nothing.
    def test_ar16_failures_are_reported_once_per_hour(self):
        self.write_stamp(epoch_minute(11, 20))
        self.set_rule(CHEAP, out("", rc=2, stderr="could not reach the database\n"))
        self.set_job("settings", out("", rc=2, stderr="could not reach the database\n"))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(2)
        self.assertIn("the Start minute could not be read", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.reset_calls()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertEqual((self.result.returncode, self.result.stdout, self.result.stderr), (0, "NOT_DUE\n", ""))
        self.assertTrue(self.cheap_only())
        # A failed run: exit 3 once; the stamp holds the slot.
        self.reset()
        self.write_stamp(epoch_minute(11, 20))
        self.set_job(f"copy --run {RUN}", job_out("copy", "failed", rc=3, run_id=RUN, state="FAILED",
                                                  error=("run_failed", "The published archive folder is missing.")))
        self.at(12, 21)
        self.archive("--scheduled")
        self.assertExit(3)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))

    # AR-17: a stamp in the future (the clock was set back) is ignored, like a first start.
    def test_ar17_future_stamp(self):
        self.write_stamp(epoch_minute(18, 20))
        self.at(12, 30)
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))


# ---------------------------------------------------------------------------
# AR-18..AR-23: the locks in both directions
# ---------------------------------------------------------------------------


class Locks(ArchiveHarness):
    # AR-18: archive.sh refuses while a release runs (manual and scheduled); the scheduled start reports it once.
    def test_ar18_release_running(self):
        (self.records / ".release.lock").mkdir(parents=True)
        self.archive()
        self.assertExit(1)
        self.assertIn("refused — a release is running", self.result.stderr)
        self.assertEqual(self.calls("docker"), [])
        self.assertFalse(self.lock.exists())
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(1)
        self.assertEqual(self.calls("docker"), [])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.assertTrue((self.records / ".release.lock").is_dir())

    # AR-19: a busy archive lock whose job container still runs (for example a long purge): the scheduled start defers
    # quietly (exit 0, the Start minute's settings read only, no record), a manual one refuses; the lock is never touched.
    def test_ar19_archive_running(self):
        self.write_lock()
        self.prepend("docker", rule(MAINTENANCE_PS, out("cid-maintenance\n")))
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "DEFERRED archive_running\n")
        self.assertEqual(self.steps(), [])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.assertEqual(self.record_dirs(), [])
        self.assertEqual(self.backup_calls(), [])
        self.archive()
        self.assertExit(1)
        self.assertIn("refused — the history archive job is running (archive.sh on bs-host, pid 4242", self.result.stderr)
        self.assertTrue((self.lock / "owner").is_file())

    # AR-19b: between two steps (owner pid alive, no job container: its pre-purge backup or the reconcile after its
    # purge) a scheduled start defers too (exit 0).
    def test_ar19b_between_two_steps(self):
        self.write_lock()
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "DEFERRED archive_running\n")
        self.assertEqual(self.steps(), [])
        self.assertTrue((self.lock / "owner").is_file())

    # AR-20: a lock whose pid is alive without a job container, or that cannot be checked, is refused, never removed.
    def test_ar20_lock_not_provably_stale(self):
        for name, lock in (("pid alive", {}), ("other host", {"host": "other-host"}), ("no owner", {"owner": False})):
            with self.subTest(case=name):
                self.reset()
                self.write_lock(**lock)
                self.archive()
                self.assertExit(1)
                self.assertIn(f"remove {self.lock.as_posix()}", self.result.stderr)
                self.assertTrue(self.lock.is_dir())
                self.assertEqual(self.steps(), [])

    # AR-21: a provably stale lock (same host, pid gone, no job container) is removed and the work runs.
    def test_ar21_stale_lock_removed(self):
        self.write_lock()
        self.prepend("ps", rule(r"^-p 4242$", out(rc=1)))
        self.archive()
        self.assertExit(0)
        self.assertIn("removed the archive lock of a run that no longer exists (archive.sh on bs-host, pid 4242",
                      self.result.stderr)
        self.assertEqual(sorted(p.name for p in self.records.iterdir() if p.name.startswith(".archive.lock")), [])
        self.assertIn("pid=", self.lock_during("recover"))
        self.assertNotIn("pid=4242\n", self.lock_during("recover"))
        self.assertFalse(self.lock.exists())

    # AR-21b: two starts judged the same lock stale; the other one took it over before this one's rename: this one
    # gives the new lock back and refuses (the rename-and-compare rule), never removing the other start's lock.
    def test_ar21b_stale_lock_taken_over_meanwhile(self):
        self.write_lock()
        owner = self.lock / "owner"
        takeover = ("open(" + repr(str(owner)) + ", 'w').write('host=bs-host\\npid=5555\\nstarted_at=2026-10-08T12:20:00Z"
                    "\\nby=archive.sh\\n')")
        self.prepend("ps", rule(r"^-p 4242$", {**out(rc=1), "run": takeover}))
        self.archive()
        self.assertExit(1)
        self.assertIn("another archive.sh took over the stale archive lock", self.result.stderr)
        self.assertIn("pid=5555\n", owner.read_text(encoding="utf-8"))
        self.assertEqual(self.steps(), [])
        self.assertEqual(sorted(p.name for p in self.records.iterdir() if p.name.startswith(".archive.lock")),
                         [".archive.lock"])

    # AR-21c: after a reboot (another boot id) or with the pid given to another process (another start time), a live
    # pid is no owner: the lock is stale, also for a scheduled start, which would otherwise defer forever.
    def test_ar21c_reboot_or_pid_reused(self):
        for name, boot, start in (("reboot", "boot-old", "777"), ("pid reused", BOOT, "111")):
            with self.subTest(case=name):
                self.reset()
                self.write_lock(boot=boot, start=start)
                self.write_stamp(epoch_minute(11, 20))
                self.at(12, 20)
                self.archive("--scheduled")
                self.assertExit(0)
                self.assertIn("removed the archive lock of a run that no longer exists", self.result.stderr)
                self.assertIn("recover", self.steps())
                self.assertNotIn("DEFERRED", self.result.stdout)

    # AR-21d: the same boot and the same start time: the owner still runs (a scheduled start defers, a manual one is
    # refused); an unreadable start time falls back to `ps -p`.
    def test_ar21d_exact_owner_still_runs(self):
        self.write_lock(boot=BOOT, start="777")
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "DEFERRED archive_running\n")
        self.archive()
        self.assertExit(1)
        self.assertIn("archive.sh holds the archive lock", self.result.stderr)
        self.reset()
        self.write_lock(pid="4243", boot=BOOT, start="777")
        self.archive()
        self.assertExit(1)
        self.assertTrue(self.lock.is_dir())

    # AR-22: a run that waits for its copy confirmation never holds the lock once archive.sh ended.
    def test_ar22_waiting_run_holds_no_lock(self):
        self.set_next(("wait", "WAITING_FOR_COPY"))
        self.archive()
        self.assertExit(0)
        self.assertFalse(self.lock.exists())


class ReleaseSide(ReleaseHarness):
    def setUp(self):
        super().setUp()
        proc = fake_proc(self.tmp / "proc", [(4242, 777)])
        with_proc(RELEASE_SH, self.release_sh, proc)

    def archive_lock(self, pid="4242", boot=None, start=None):
        lock = self.records / ".archive.lock"
        lock.mkdir(parents=True)
        text = f"host=rs-host\npid={pid}\nstarted_at=2026-10-08T10:20:00Z\nby=archive.sh\n"
        text += "" if boot is None else f"boot={boot}\n"
        text += "" if start is None else f"start={start}\n"
        (lock / "owner").write_text(text, encoding="utf-8", newline="\n")
        return lock

    # AR-23b: release.sh judges the owner as archive.sh does: the same boot and start time still run (refused); after
    # a reboot or with the pid given to another process the lock is stale and the release continues.
    def test_ar23b_boot_id_and_start_time(self):
        lock = self.archive_lock(boot=BOOT, start="777")
        self.prepend("docker", rule(MAINTENANCE_PS, out("")))
        self.release()
        self.assertEqual(self.result.returncode, 1, self.result.stderr)
        self.assertIn("release: refused — the history archive job holds its lock", self.result.stderr)
        for name, boot, start in (("reboot", "boot-old", "777"), ("pid reused", BOOT, "111")):
            with self.subTest(case=name):
                self.reset()
                lock = self.archive_lock(boot=boot, start=start)
                self.prepend("docker", rule(MAINTENANCE_PS, out("")))
                self.release()
                self.assertExit(0, "completed")
                self.assertIn("was left by an archive.sh that no longer runs", self.result.stderr)
                self.assertTrue(lock.is_dir())

    # AR-23: release.sh refuses while archive.sh holds the archive lock (before any record or Docker command): during
    # a purge (its job container runs) as between two steps; it continues past a lock its owner provably left behind.
    def test_ar23_release_refuses_while_archiving(self):
        lock = self.archive_lock()
        self.release()
        self.assertEqual(self.result.returncode, 1, self.result.stderr)
        self.assertIn(f"release: refused — the history archive job holds its lock {lock.as_posix()} (archive.sh on"
                      " rs-host, pid 4242, since 2026-10-08T10:20:00Z).", self.result.stderr)
        self.assertEqual(self.calls("docker"), [])
        self.assertFalse((self.records / ".release.lock").exists())
        self.assertTrue((lock / "owner").is_file())
        self.assertFalse(list(self.records.glob("*-*/record.json")))
        # A job container (a long purge) still runs although the pid is gone: still refused.
        self.prepend("ps", rule(r"^-p 4242$", out(rc=1)))
        self.prepend("docker", rule(MAINTENANCE_PS, out("cid-maintenance\n")))
        self.release()
        self.assertEqual(self.result.returncode, 1, self.result.stderr)
        self.assertTrue(lock.is_dir())

    def test_ar23_stale_archive_lock_does_not_block(self):
        lock = self.archive_lock()
        self.prepend("ps", rule(r"^-p 4242$", out(rc=1)))
        self.prepend("docker", rule(MAINTENANCE_PS, out("")))
        self.release()
        self.assertExit(0, "completed")
        self.assertIn("was left by an archive.sh that no longer runs", self.result.stderr)
        self.assertTrue(lock.is_dir())


# ---------------------------------------------------------------------------
# AR-24: pf archive
# ---------------------------------------------------------------------------


class PfArchive(PfHarness):
    def setUp(self):
        super().setUp()
        self.wrapper(self.root / "deploy" / "production" / "archive.sh",
                     f'exec "{self.python}" "{self.fake.as_posix()}" "{self.state.as_posix()}" archive.sh "$@"')
        self.rules["archive.sh"] = [rule(r".*")]

    def test_ar24_pf_archive_runs_archive_sh(self):
        self.pf("archive")
        self.assertExit(0)
        self.assertEqual(self.stub_calls("archive.sh"), [["--env-file", ".env.production"]])
        self.assertEqual(self.calls("docker"), [])
        self.reset()
        self.wrapper(self.root / "deploy" / "production" / "archive.sh",
                     f'exec "{self.python}" "{self.fake.as_posix()}" "{self.state.as_posix()}" archive.sh "$@"')
        self.rules["archive.sh"] = [rule(r".*", out("", rc=4))]
        self.pf("archive", "--env-file", ".env.production")
        self.assertExit(4)
        self.pf("archive", "extra")
        self.assertExit(2)
        self.assertIn("pf archive takes no argument extra.", self.result.stderr)
        help_text = self.pf("--help").stdout
        self.assertIn("backup and PURGE its archived Movement history", help_text)
        self.assertIn("carry out an open\n                            permanent deletion request", help_text)
        self.assertNotIn("never deletes", help_text)
        # Step 4: pf archive runs the hourly work, the evaluation of the automatic runs included.
        self.assertIn("evaluates the automatic runs: when Administration has them on", help_text)
        # Step 5: and the viewing work before the work on a run.
        self.assertIn("Before the work on a run, it removes the loaded\n                            rows of a view of an"
                      " archive that was closed or whose days ran out, and loads an archive", help_text)


# ---------------------------------------------------------------------------
# AR-25..AR-28: check.sh archival_proposal
# ---------------------------------------------------------------------------


def archive_status(findings=(), rc=None, open_run=None, last=None, copy=False, receipts=(), open_deletion=None,
                   last_deletion=None, automatic=AUTOMATIC_OFF, views=(), not_closed=(), viewer_bytes=106496):
    """A `history-archive status` report. Step 5 (contract B5 §3, §6): VIEWS, the views not finished (open_views, each
    with age_seconds); NOT_CLOSED, the ids of views_not_closed; VIEWER_BYTES, the space of the viewer tables (by default
    that of empty viewer tables and their indexes)."""
    rc = (1 if findings else 0) if rc is None else rc
    return job_doc("status", "finding" if findings else "ok", rc=rc, open_run=open_run, last_finished_run=last,
                   archive_dir={"path": "/archive", "usable": "archive_dir_unusable" not in findings,
                                "free_bytes": 5 * 1073741824, "total_bytes": 10 * 1073741824, "partial": []},
                   copy_dir=({"path": "/archive-copy", "usable": True, "free_bytes": 1073741824,
                              "total_bytes": 2 * 1073741824, "partial": []} if copy else None),
                   receipts_not_copied=list(receipts), open_deletion_request=open_deletion,
                   last_finished_deletion_request=last_deletion, open_views=list(views),
                   views_not_closed=list(not_closed), viewer_bytes=viewer_bytes,
                   view_settings={"days": 7, "max_loaded": 1}, automatic_runs=automatic, max_wait_days=7,
                   findings=list(findings))


ARCHIVE_STATUS_RUN = (r"--profile ops run --rm --no-deps -T --user 1000:1000 --name"
                      r" partflow-production-archive-check-[0-9]+ maintenance status$")


class CheckArchival(CheckHarness):
    def setUp(self):
        super().setUp()
        self.archive_dir = self.tmp / "archive"
        self.archive_dir.mkdir()
        self.write_env([*self.env_lines, f"PARTFLOW_ARCHIVE_DIR={sh_path(self.archive_dir)}"])
        self.prepend("df", rule(r"^-Pk .*archive$", out(df_text(10 * 1048576, 90 * 1048576))))

    def archival(self, document, rc):
        self.prepend("docker", rule(ARCHIVE_STATUS_RUN, out(document, rc=rc)))
        self.check()
        return self.line("archival_proposal")

    def archive_runs(self):
        return [c for c in self.docker() if re.search(ARCHIVE_STATUS_RUN, c)]

    # AR-25: PASS from `history-archive status` (the open run, the last finished run with its result, the free space).
    def test_ar25_pass(self):
        last = run_object("COMPLETED", result="EXPORT_ONLY")
        waiting = {**run_object("WAITING_FOR_COPY"), "id": 4, "age_seconds": 90000, "waiting_seconds": 2 * 86400}
        text = self.archival(archive_status(open_run=waiting, last=last), 0)
        self.assertEqual(text, "PASS archival_proposal run 4 WAITING_FOR_COPY waiting 2.0 days for its copy"
                               " confirmation (limit 7 days); last finished run 3 COMPLETED (EXPORT_ONLY); archive folder"
                               " 5.0 GiB free")
        self.assertExit(0)
        runs = self.archive_runs()
        self.assertEqual(len(runs), 1)
        self.assertNotIn(" -v ", runs[0])
        self.assertTrue((self.monitoring / "archive-status.json").is_file())
        # The `status` report's own archival line is replaced, never printed twice.
        self.assertEqual(len([t for t in self.lines() if " archival_proposal " in t]), 1)

    # AR-26: FAIL naming every finding.
    def test_ar26_findings(self):
        last = run_object("FAILED", failure_reason="The host job stopped while the run was RUNNING.")
        waiting = {**run_object("WAITING_FOR_COPY"), "id": 4, "age_seconds": 900000, "waiting_seconds": 9 * 86400}
        text = self.archival(archive_status(findings=("copy_confirmation_overdue", "last_run_failed",
                                                      "copy_dir_unusable"), open_run=waiting, last=last), 1)
        self.assertExit(1)
        self.assertTrue(text.startswith("FAIL archival_proposal copy_confirmation_overdue: run 4 WAITING_FOR_COPY has"
                                        " waited 9.0 days for its off-host copy confirmation (limit 7 days);"
                                        " last_run_failed: run 3 FAILED: The host job stopped"), text)
        self.assertIn("copy_dir_unusable: the copy folder (PARTFLOW_ARCHIVE_COPY_DIR)", text)
        self.reset()
        text = self.archival(archive_status(findings=("archive_dir_unusable",)), 1)
        self.assertTrue(text.startswith("FAIL archival_proposal archive_dir_unusable: "), text)
        self.reset()
        text = self.archival("Error response from daemon: no such image\n", 2)
        self.assertEqual(text, "FAIL archival_proposal history-archive status could not run (exit 2); see"
                               " archive-status.err")

    # AR-27b: a timed-out check removes only its own named container, never an archive.sh step's container.
    def test_ar27b_timeout_removes_only_its_own_run(self):
        self.prepend("timeout", rule(r"^120 docker compose .* maintenance status$", out(rc=124)))
        self.prepend("docker", rule(r"^rm -f partflow-production-archive-check-[0-9]+$"))
        self.check()
        self.assertEqual(self.line("archival_proposal"),
                         "FAIL archival_proposal history-archive status gave no answer within 120 s")
        removed = [c for c in self.docker() if c.startswith("rm ")]
        self.assertEqual(len(removed), 1, removed)
        self.assertRegex(removed[0], r"^rm -f partflow-production-archive-check-[0-9]+$")
        self.assertFalse([c for c in self.docker() if "service=maintenance" in c], self.docker())

    # AR-27: SKIP while archival is not set up; FAIL for a folder that does not exist (no Docker run).
    def test_ar27_not_set_up(self):
        self.write_env(self.env_lines)
        self.check()
        self.assertEqual(self.line("archival_proposal"),
                         "SKIP archival_proposal archival is not set up: PARTFLOW_ARCHIVE_DIR is empty in .env.production"
                         " (DEPLOYMENT §3.1)")
        self.assertEqual(self.archive_runs(), [])
        self.write_env([*self.env_lines, f"PARTFLOW_ARCHIVE_DIR={sh_path(self.tmp)}/missing"])
        self.check("--only", "archival_proposal")
        self.assertTrue(self.line("archival_proposal").startswith("FAIL archival_proposal archive_dir_unusable: the"
                                                                  " archive folder"), self.line("archival_proposal"))

    def configure_copy(self, mount):
        copy = self.tmp / "archive-copy"
        copy.mkdir(exist_ok=True)
        self.write_env([*self.env_lines, f"PARTFLOW_ARCHIVE_DIR={sh_path(self.archive_dir)}",
                        f"PARTFLOW_ARCHIVE_COPY_DIR={sh_path(copy)}"])
        self.prepend("df", rule(r"^-P .*archive-copy$", out(df_text(1, 2, mount))),
                     rule(r"^-P .*archive$", out(df_text(1, 2, "/volume1"))))

    # AR-28: the copy folder is mounted for status exactly as archive.sh mounts it (the override file, never -v); a
    # copy folder on `/` is not mounted and the FAIL line gives the reason; a release lock skips the run.
    def test_ar28_copy_folder_and_release_guard(self):
        self.configure_copy("/mnt/usb")
        text = self.archival(archive_status(copy=True), 0)
        self.assertTrue(text.startswith("PASS archival_proposal no open run; no finished run; archive folder 5.0 GiB"
                                        " free; copy folder 1.0 GiB free"), text)
        self.assertRegex(self.archive_runs()[0], r" -f \S*copy-mount\.yaml --env-file ")
        self.assertNotIn(" -v ", self.archive_runs()[0])
        self.reset()
        self.configure_copy("/")
        text = self.archival(archive_status(findings=("copy_dir_unusable",)), 1)
        self.assertRegex(text, r"^FAIL archival_proposal copy_dir_unusable: the copy folder \(PARTFLOW_ARCHIVE_COPY_DIR\)"
                               r" \S*archive-copy is on the host's root file system \(/\), not on another device")
        self.assertNotRegex(self.archive_runs()[0], r"copy-mount\.yaml| -v ")
        self.reset()
        self.configure_copy("/mnt/usb")
        text = self.archival(archive_status(copy=True), 0)
        lock = self.records_dir / ".release.lock"
        lock.mkdir(parents=True)
        os.utime(lock, (CHECK_EPOCH - 60, CHECK_EPOCH - 60))
        self.state.joinpath("calls.jsonl").unlink()
        self.check()
        self.assertTrue(self.line("archival_proposal").startswith("SKIP archival_proposal a release is running"))
        self.assertEqual(self.archive_runs(), [])

    # AR-40 (step 2): the purge receipt of a run that the job copied off the host is missing in the copy folder.
    def test_ar40_receipt_not_copied(self):
        text = self.archival(archive_status(findings=("receipt_not_copied",), receipts=(ARCHIVE_NAME,)), 1)
        self.assertEqual(text, f"FAIL archival_proposal receipt_not_copied: the purge receipt of {ARCHIVE_NAME} is"
                               " missing or differs in the copy folder; the next archive.sh start copies it again")
        self.assertExit(1)

    # AR-41 (step 2): archive.sh's incident record (a reconcile after a purge that did not end clean) fails the line
    # first, also while the status report is clean; without the record the line passes again.
    def test_ar41_incident_record(self):
        incident = self.records_dir / "archive" / "reconcile-after-purge"
        incident.parent.mkdir(parents=True)
        incident.write_text("result=mismatch\nexit_code=1\nrun=3\narchive_name=run-3-20261008T122000Z\n"
                            f"pre_purge_backup={BACKUP}\nfailing_checks=c g\nfinished_at=2026-10-08T12:21:00Z\n"
                            "report=/root/partflow-deployments/archive/20261008T122030Z/reconcile.json\n",
                            encoding="utf-8", newline="\n")
        text = self.archival(archive_status(), 0)
        self.assertEqual(text, "FAIL archival_proposal reconcile_after_purge: the reconcile after the purge of run 3"
                               " found a mismatch in checks c g at 2026-10-08T12:21:00Z: an incident, OPERATIONS_RUNBOOK"
                               " §8 (Reconcile finding after a purge); report"
                               " /root/partflow-deployments/archive/20261008T122030Z/reconcile.json")
        self.assertExit(1)
        self.assertTrue(incident.is_file())
        self.reset()
        incident = self.records_dir / "archive" / "reconcile-after-purge"
        incident.parent.mkdir(parents=True)
        incident.write_text("result=could_not_run\nexit_code=2\nrun=3\nfailing_checks=\n", encoding="utf-8")
        text = self.archival(archive_status(findings=("last_run_failed",), last=run_object("FAILED")), 1)
        self.assertTrue(text.startswith("FAIL archival_proposal reconcile_after_purge: the reconcile after the purge of"
                                        " run 3 did not complete (could_not_run, exit 2): an incident"), text)
        self.assertIn("; last_run_failed: run 3 FAILED", text)

    # AR-42 (step 2): PASS names a PURGING open run's progress and the last run's result, purge and skips.
    def test_ar42_purging_and_skips(self):
        purging = run_object("PURGING", purged={"components": 40, "flows": 90, "movements": 1200})
        last = run_object("COMPLETED_WITH_SKIPS", result="PURGED", skipped_components=2, skipped_flows=5,
                          purged={"components": 118, "flows": 305, "movements": 4190})
        text = self.archival(archive_status(open_run={**purging, "id": 4}, last=last), 0)
        self.assertEqual(text, "PASS archival_proposal run 4 PURGING (40 components, 90 flows, 1200 Movements purged so"
                               " far); last finished run 3 COMPLETED_WITH_SKIPS (PURGED: 118 components, 305 flows, 4190"
                               " Movements purged; 2 components, 5 flows skipped); archive folder 5.0 GiB free")

    # AR-55 (step 3): an open deletion request, APPROVED or RUNNING, requested longer ago than --deletion-wait-minutes
    # (default 15) fails the line: the history archive task has not carried it out, or has not finished it (review
    # finding 3: a request stuck RUNNING); an APPROVED request behind a run that is RUNNING or PURGING names that run. A
    # younger request and a FAILED last request pass, and the PASS line names them as archive.sh's DELETION line does.
    def test_ar55_deletion_request_waiting(self):
        waiting = {**request_object("APPROVED"), "age_seconds": 16 * 60}
        text = self.archival(archive_status(open_deletion=waiting), 0)
        self.assertEqual(text, "FAIL archival_proposal deletion_request_waiting: deletion request 7 APPROVED has waited"
                               " 16 min (limit 15 min), and the history archive task has not carried it out: is the task"
                               " enabled? (SYNOLOGY_NAS §11.8)")
        self.assertExit(1)
        self.reset()
        self.prepend("docker", rule(ARCHIVE_STATUS_RUN, out(archive_status(open_deletion=waiting), rc=0)))
        self.check("--deletion-wait-minutes", "30")
        self.assertEqual(self.line("archival_proposal"), "PASS archival_proposal no open run; no finished run; archive"
                                                         " folder 5.0 GiB free; deletion request 7 APPROVED for 16 min")
        self.reset()
        running = {**request_object("RUNNING"), "age_seconds": 2 * 60}
        failed = request_object("FAILED", request_id=6, failure_reason="The data changed after the request.")
        text = self.archival(archive_status(open_deletion=running, last_deletion=failed), 0)
        self.assertEqual(text, "PASS archival_proposal no open run; no finished run; archive folder 5.0 GiB free;"
                               " deletion request 7 RUNNING for 2 min; last deletion request 6 FAILED")
        self.assertExit(0)
        with self.subTest(case="a request stuck RUNNING"):
            self.reset()
            stuck = {**request_object("RUNNING"), "age_seconds": 40 * 60}
            text = self.archival(archive_status(open_deletion=stuck, last_deletion=failed), 0)
            self.assertEqual(text, "FAIL archival_proposal deletion_request_waiting: deletion request 7 RUNNING has been"
                                   " open for 40 min (limit 15 min), and the history archive task has not finished it:"
                                   " is the task enabled? (SYNOLOGY_NAS §11.8)")
            self.assertExit(1)
        with self.subTest(case="an APPROVED request behind a RUNNING run"):
            self.reset()
            run = {**run_object("RUNNING"), "id": 4, "age_seconds": 50 * 60}
            text = self.archival(archive_status(open_run=run, open_deletion=waiting), 0)
            self.assertEqual(text, "FAIL archival_proposal deletion_request_waiting: deletion request 7 APPROVED has"
                                   " waited 16 min (limit 15 min) behind run 4 RUNNING: the history archive task carries"
                                   " it out once that run's work in progress ends; is the task enabled? (SYNOLOGY_NAS"
                                   " §11.8)")
            self.assertExit(1)
        for value in ("0", "1441", "x"):
            with self.subTest(value=value):
                self.check("--deletion-wait-minutes", value)
                self.assertExit(2)
                self.assertIn("--deletion-wait-minutes must be", self.result.stdout + self.result.stderr)

    # AR-56 (step 3): archive.sh's incident record after a deletion names the request and its runbook entry.
    def test_ar56_incident_after_a_deletion(self):
        incident = self.records_dir / "archive" / "reconcile-after-purge"
        incident.parent.mkdir(parents=True)
        incident.write_text(f"result=mismatch\nexit_code=1\nrun=\narchive_name=\nrequest={REQ}\n"
                            f"pre_purge_backup={DEL_BACKUP}\nfailing_checks=c\nfinished_at=2026-10-08T12:21:00Z\n"
                            "report=/root/partflow-deployments/archive/20261008T122030Z/reconcile.json\n",
                            encoding="utf-8", newline="\n")
        text = self.archival(archive_status(), 0)
        self.assertEqual(text, "FAIL archival_proposal reconcile_after_purge: the reconcile after the deletion of request"
                               " 7 found a mismatch in checks c at 2026-10-08T12:21:00Z: an incident, OPERATIONS_RUNBOOK"
                               " §8 (Reconcile finding after a deletion); report"
                               " /root/partflow-deployments/archive/20261008T122030Z/reconcile.json")

    # AR-57 (step 3, review finding 2): while archival is not set up, PartFlow still records deletion requests and
    # nothing carries them out. check.sh runs the cheap check (exec in the running backend container, bounded by 30 s,
    # its report kept in the state folder): a request requested longer ago than --deletion-wait-minutes fails the line;
    # a younger request, a failed read and no answer keep the SKIP, with the reason.
    def test_ar57_waiting_request_without_archival(self):
        skip = ("SKIP archival_proposal archival is not set up: PARTFLOW_ARCHIVE_DIR is empty in .env.production"
                " (DEPLOYMENT §3.1)")
        # The report started at 2026-10-08T12:20:00Z (job_doc): requested 20 minutes before it.
        old = request_object("APPROVED", requested_at="2026-10-08T12:00:00.000000Z")
        settings = re.escape("exec -T backend python -m app.cli history-archive settings") + "$"
        cases = (
            ("waiting", settings_out(request=old), (), 1,
             "FAIL archival_proposal deletion_request_waiting: deletion request 7 APPROVED has waited 20 min (limit 15"
             " min), but history archival is not set up (PARTFLOW_ARCHIVE_DIR is empty), so nothing carries it out: set"
             " up the history archive task (SYNOLOGY_NAS §11.8) or cancel the request in PartFlow"),
            ("a longer limit", settings_out(request=old), ("--deletion-wait-minutes", "30"), 0, skip),
            ("no request", settings_out(), (), 0, skip),
            ("failed read", out("", rc=1, stderr='service "backend" is not running\n'), (), 0,
             f"{skip}; the deletion request check (history-archive settings in the backend container) could not run"
             " (exit 1); see archive-settings.err"),
        )
        for name, answer, options, code, expected in cases:
            with self.subTest(case=name):
                self.reset()
                self.write_env(self.env_lines)
                self.prepend("docker", rule(settings, answer))
                self.check(*options)
                self.assertEqual(self.line("archival_proposal"), expected)
                self.assertExit(code)
                self.assertEqual(self.archive_runs(), [])
                self.assertTrue((self.monitoring / "archive-settings.json").is_file())
                bounded = [argv[0] for argv in self.timeout_calls() if argv[-3:] == ["app.cli", "history-archive",
                                                                                      "settings"]]
                self.assertEqual(bounded, ["30"])
        with self.subTest(case="no answer within 30 s"):
            self.reset()
            self.write_env(self.env_lines)
            self.prepend("timeout", rule(r"^30 docker compose .* " + settings, out(rc=124)))
            self.check()
            self.assertEqual(self.line("archival_proposal"), f"{skip}; the deletion request check (history-archive"
                                                             " settings in the backend container) gave no answer within"
                                                             " 30 s")
            self.assertExit(0)

    # AR-64 (step 4, contract B4 §6): the reminders of the automatic runs. A PROPOSED run passes while it waits for its
    # approval no longer than the setting (max_wait_days) and says how long it has waited (age_seconds); then it fails as
    # approval_overdue. A run that failed because the User of its authority may no longer run it is named once, by
    # authorization_failed instead of last_run_failed; standing_authorization_invalid gives the authorization's problem.
    def test_ar64_automatic_run_reminders(self):
        proposed = {**run_object("PROPOSED", trigger="AUTOMATIC", reason=AUTOMATIC_REASON), "id": 7,
                    "age_seconds": 2 * 86400}
        text = self.archival(archive_status(open_run=proposed), 0)
        self.assertEqual(text, "PASS archival_proposal run 7 PROPOSED waiting 2.0 days for approval (limit 7 days); no"
                               " finished run; archive folder 5.0 GiB free")
        self.assertExit(0)
        self.reset()
        overdue = {**proposed, "age_seconds": 8 * 86400 + 8640}
        text = self.archival(archive_status(findings=("approval_overdue",), open_run=overdue), 1)
        self.assertEqual(text, "FAIL archival_proposal approval_overdue: run 7 PROPOSED has waited 8.1 days for approval"
                               " (limit 7 days); approve or dismiss it in Administration → History archival & purge")
        self.assertExit(1)
        self.reset()
        reason = ("The User who switched off the approval step of automatic runs, Ann (user 4), is no longer active, so"
                  f" the history of run {RUN} was not purged.")
        failed = automatic_run("FAILED", authorization_failed=True, failure_reason=reason,
                               finished_at="2026-10-08T12:20:40Z")
        text = self.archival(archive_status(findings=("last_run_failed", "authorization_failed"), last=failed), 1)
        self.assertEqual(text, f"FAIL archival_proposal authorization_failed: run {RUN} FAILED: {reason}")
        self.reset()
        problem = ("The User who switched off the approval step of automatic runs, Ann (user 4), no longer holds Run"
                   " history archival and purge. No automatic run is created until a holder of the key saves the"
                   " approval setting again.")
        automatic = {"enabled": True, "approval_required": False,
                     "standing_authorization": {"audit_event_id": 12,
                                                "authorized_by": {"user_id": 4, "display_name": "Ann"},
                                                "authorized_at": "2026-10-01T09:00:00.000000Z",
                                                "reason": "Weekly archival without approval", "valid": False,
                                                "problem": problem}}
        text = self.archival(archive_status(findings=("standing_authorization_invalid",), automatic=automatic), 1)
        self.assertEqual(text, f"FAIL archival_proposal standing_authorization_invalid: {problem}")
        self.assertExit(1)

    # AR-75 (step 5, contract B5 §6, lead decision 5): the views of an archive. The PASS line names each view that is not
    # finished, an OPEN one with the time its days run out, and the space of the viewer tables; a view that waits to be
    # loaded (REQUESTED) is no finding. view_not_closed fails the line with the view, its state and since when: the job
    # could not remove its loaded rows.
    def test_ar75_views_of_an_archive(self):
        opened = {**view_object("OPEN"), "age_seconds": 4 * 3600}
        waiting = {**view_object("REQUESTED", view_id=5, run_id=3), "age_seconds": 120}
        text = self.archival(archive_status(views=(opened, waiting), viewer_bytes=1395864371), 0)
        self.assertEqual(text, "PASS archival_proposal no open run; no finished run; archive folder 5.0 GiB free; view 4"
                               " of run 2 OPEN until 2026-10-15T12:21:03Z; view 5 of run 3 REQUESTED; viewer tables 1.3"
                               " GiB")
        self.assertExit(0)
        self.reset()
        # No view: the PASS line stays as it was (the empty viewer tables are not named).
        text = self.archival(archive_status(), 0)
        self.assertEqual(text, "PASS archival_proposal no open run; no finished run; archive folder 5.0 GiB free")
        self.reset()
        expired = {**view_object("OPEN"), "open_until": "2026-10-08T10:00:00.000000Z", "age_seconds": 8 * 86400}
        text = self.archival(archive_status(findings=("view_not_closed",), views=(expired,), not_closed=(VIEW,),
                                            viewer_bytes=5 * 1048576), 1)
        self.assertEqual(text, "FAIL archival_proposal view_not_closed: view 4 of run 2 (OPEN, open until"
                               " 2026-10-08T10:00:00Z) was not closed by the history archive job; its loaded rows still"
                               " take database space (OPERATIONS_RUNBOOK §11)")
        self.assertExit(1)
        self.reset()
        closing = {**view_object("CLOSING", view_id=CLOSED_VIEW), "age_seconds": 3 * 86400}
        text = self.archival(archive_status(findings=("view_not_closed",), views=(closing, expired),
                                            not_closed=(CLOSED_VIEW, VIEW)), 1)
        self.assertEqual(text, "FAIL archival_proposal view_not_closed: view 3 of run 2 (CLOSING, closing since"
                               " 2026-10-08T12:30:00Z), view 4 of run 2 (OPEN, open until 2026-10-08T10:00:00Z) were not"
                               " closed by the history archive job; their loaded rows still take database space"
                               " (OPERATIONS_RUNBOOK §11)")


# ---------------------------------------------------------------------------
# AR-29..AR-39: build step 2, the pre-purge backup, the purge and the reconcile after it
# ---------------------------------------------------------------------------


class Purge(ArchiveHarness):
    # AR-29: the pre-purge backup waits up to --backup-wait-minutes for a running backup (backup.sh
    # --lock-wait-minutes; default 60, 0 refuses at once).
    def test_ar29_backup_wait_option(self):
        for arguments, minutes in (((), "60"), (("--backup-wait-minutes", "5"), "5"),
                                   (("--backup-wait-minutes", "0"), "0")):
            with self.subTest(arguments=arguments):
                self.reset()
                self.set_next(("backup", "RUNNING"))
                self.archive(*arguments)
                self.assertExit(0)
                self.assertEqual(self.backup_calls(), [BACKUP_ARGUMENTS + [minutes]])
                self.assertIn(f"it waits up to {minutes} min for a backup that is running", self.result.stderr)

    # AR-30: the pre-purge backup fails (a backup still running after the wait, a stale lock, no space, a dump error):
    # nothing is purged or reconciled, exit 2, and the message says what happens to the run.
    def test_ar30_backup_failure(self):
        refused = ("backup: refused — a backup is running (backup.sh on bs-host, pid 7, since 2026-10-08T12:00:00Z),"
                   " still after a wait of 60 min. Nothing was written.\n")
        for name, answer, why in (
            ("refused after the wait", out("", rc=1, stderr=refused), "the pre-purge backup failed (backup.sh exit 1;"),
            ("dump failed", out("", rc=3, stderr="backup: pg_dump failed (exit 1). No backup was published.\n"),
             "the pre-purge backup failed (backup.sh exit 3;"),
            ("no dump start", out(f"BACKUP {BACKUP} /backups/{BACKUP}\n"),
             "backup.sh did not name the pre-purge backup with its dump start"),
            ("another run's backup", backup_line(name="20261008T122030Z-pre-purge-run-4-20261008T122000Z"),
             "backup.sh did not name the pre-purge backup with its dump start"),
        ):
            with self.subTest(case=name):
                self.reset()
                self.set_next(("backup", "RUNNING"))
                self.rules["backup.sh"] = [rule(r".*", answer)]
                self.archive()
                self.assertExit(2)
                self.assertEqual(self.order(), ["recover", "evaluate", "next", "backup.sh", "status"])
                self.assertEqual(self.result.stdout, "")
                self.assertIn(f"archive: run {RUN}: {why}", self.result.stderr)
                self.assertIn("Nothing was deleted. The run stays RUNNING, and the next start fails it (recover)."
                              f" {REQUEST_NEW}", self.result.stderr)
                self.assertEqual(len(self.record_dirs()), 1)
                self.assertFalse(self.incident.exists())

    # AR-31: a run left PURGING by an earlier start is resumed by purge alone, without a backup, and reconciled.
    def test_ar31_resumed_purge(self):
        self.set_next(("purge", "PURGING"))
        self.set_purge(purge_out(resumed=True))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"purge --run {RUN}", "reconcile", "status"])
        self.assertEqual(self.backup_calls(), [])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertIn(f"run {RUN} was left PURGING by an earlier start: its purge resumes without a new backup",
                      self.result.stderr)
        self.assertIn("the reconcile after the purge of run 3 is clean", self.result.stderr)

    # AR-32: exit 3 from `next` (the requester re-check failed the run before its backup): no backup, no purge.
    def test_ar32_requester_check_fails_the_run(self):
        reason = ("The User who requested run 3, Ann (user 1), is no longer active, so its history was not purged."
                  " Nothing was deleted; a holder of Run history archival and purge may request a new run.")
        self.set_next(("backup", "FAILED", 3, ("run_failed", reason)))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
        self.assertEqual(self.backup_calls(), [])
        self.assertIn(f"archive: run {RUN} FAILED: {reason} Nothing was deleted. {REQUEST_NEW}", self.result.stderr)

    # AR-33: a reconcile finding after a purge is an incident: exit 4, no ARCHIVE line, the incident record; the next
    # start fails closed (AR-38).
    def test_ar33_reconcile_finding_after_a_purge(self):
        self.set_rule(RECONCILE, out(reconcile_doc(1, failing=("c", "g")), rc=1))
        self.archive()
        self.assertExit(4)
        self.assertEqual(self.result.stdout, "")
        self.assertEqual(self.order()[-3:], [PURGE_WITH_BACKUP, "reconcile", "status"])
        record = self.record_dirs()[0]
        self.assertIn(f"archive: INCIDENT — the reconcile after the purge of run {RUN} found a mismatch (checks c g):"
                      f" {record.as_posix()}/reconcile.json. Stop requesting runs and deletions and follow"
                      f" OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge): the purge receipt is"
                      f" {sh_path(self.archive_dir)}/{ARCHIVE_NAME}.purged.json and the pre-purge backup {BACKUP}",
                      self.result.stderr.replace(sh_path(record), record.as_posix()))
        values = self.incident_values()
        self.assertEqual({k: values[k] for k in ("result", "exit_code", "run", "archive_name", "request",
                                                 "pre_purge_backup", "failing_checks")},
                         {"result": "mismatch", "exit_code": "1", "run": "3", "archive_name": ARCHIVE_NAME,
                          "request": "", "pre_purge_backup": BACKUP, "failing_checks": "c g"})
        self.assertRegex(values["finished_at"], r"^2026-10-08T12:20:30Z$")
        self.assertTrue(values["report"].endswith("/reconcile.json"), values["report"])
        self.assertIn("records the incident, and no start works on a run, a deletion request or a view until you remove it"
                      " after the investigation.", self.result.stderr)
        # The next start, with another run to work on, runs recover and status only; the record stays.
        self.rules["docker"] = default_jobs()
        self.reset_calls()
        self.at(13, 20)
        self.archive()
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertEqual(self.incident_values()["failing_checks"], "c g")

    # AR-33b: a reconcile that could not run, or ended in error, after a purge is an incident too.
    def test_ar33b_reconcile_could_not_run(self):
        for name, answer, result, what in (
            ("no report", out("", rc=2, stderr="could not reach the database\n"), "could_not_run",
             "could not run (exit 2, no complete report)"),
            ("error", out(reconcile_doc(2, errors=("c",)), rc=2), "error", "ended in error (checks c)"),
            ("exit code differs", out(reconcile_doc(0), rc=1), "could_not_run", "could not run (exit 1"),
        ):
            with self.subTest(case=name):
                self.reset()
                self.set_rule(RECONCILE, answer)
                self.archive()
                self.assertExit(4)
                self.assertIn(f"the reconcile after the purge of run {RUN} {what}", self.result.stderr)
                self.assertEqual(self.incident_values()["result"], result)

    # AR-34: a purge that fails once the run is purging (exit 3, the report names its pre-purge backup) is reconciled:
    # exit 3 when clean, 4 with a finding; one that fails before (no backup in its report) deleted nothing.
    def test_ar34_failed_purge(self):
        reason = ("A purge batch deleted 9 Movements of components 12, but their records hold 10. The batch was rolled"
                  " back; nothing of it was deleted.")
        self.set_purge(purge_out(rc=3, state="FAILED", error=("run_failed", reason)))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.order()[-3:], [PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertIn(f"run {RUN} FAILED while purging: {reason} Whole components only were deleted; the purge receipt"
                      f" {sh_path(self.archive_dir)}/{ARCHIVE_NAME}.purged.json lists what was deleted",
                      self.result.stderr)
        self.assertFalse(self.incident.exists())
        self.reset()
        self.set_purge(purge_out(rc=3, state="FAILED", error=("run_failed", reason)))
        self.set_rule(RECONCILE, out(reconcile_doc(1, failing=("g",)), rc=1))
        self.archive()
        self.assertExit(4)
        self.assertEqual(self.incident_values()["failing_checks"], "g")
        self.reset()
        problem = ("The published archive run-3-20261008T122000Z changed after its verification (its manifest is not"
                   " the one the export recorded). Nothing was deleted; request a new run.")
        self.set_purge(purge_out(rc=3, state="FAILED", started=False, error=("run_failed", problem)))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.order()[-2:], [PURGE_WITH_BACKUP, "status"])
        self.assertIn(f"run {RUN} FAILED: {problem} Nothing was deleted.", self.result.stderr)

    # AR-34b: a refused or unfinished purge (exit 1 or 2) is never reconciled; the message says what the next start does.
    # Exit 2 while PURGING (design §7.5: a lost connection or another transient database error rolls the batch in
    # progress back and leaves the run PURGING) defers the reconcile to the resumed purge, also when the purge was a
    # resumed one and also when its report names no state (the run was PURGING before it); exit 2 before the run became
    # PURGING deleted nothing.
    def test_ar34b_purge_refused_or_unfinished(self):
        stays = ("run 3 stays PURGING: the batch in progress was rolled back (whole components only are ever deleted),"
                 " and the next start resumes the purge and reconciles when it ends.")
        resumed = (("purge", "PURGING"),)
        for name, nexts, answer, code, texts in (
            ("refused", (), purge_out(rc=1, state="RUNNING", started=False, error=(
                "backup_invalid", "The dump of x started at y, not after the verification of run 3.")), 1,
             ["Nothing was deleted. Run 3 stays RUNNING, and the next start fails it (recover)."]),
            ("database lost while purging", (), purge_out(rc=2, state="PURGING", error=(
                "outcome_unknown", "PartFlow could not reach its database.")), 2,
             ["the purge of run 3 stopped: PartFlow could not reach its database.", stays]),
            ("a deadlock in a resumed purge", resumed, purge_out(rc=2, state="PURGING", resumed=True, error=(
                "outcome_unknown", "A purge batch of run 3 met a deadlock and was rolled back.")), 2,
             ["the purge of run 3 stopped: A purge batch of run 3 met a deadlock and was rolled back.", stays]),
            ("a resumed purge whose report names no state", resumed, purge_out(rc=2, state=None, resumed=True, error=(
                "database_unavailable", "PartFlow could not reach its database.")), 2, [stays]),
            ("database lost before purging", (), purge_out(rc=2, state="RUNNING", started=False, error=(
                "database_unavailable", "PartFlow could not reach its database.")), 2,
             ["history-archive purge could not run: PartFlow could not reach its database.",
              "Nothing was deleted. Run 3 stays RUNNING, and the next start fails it (recover)."]),
            ("no report", (), out("", rc=137), 2,
             ["the outcome of the purge of run 3 is unknown. Whole components only are ever deleted: the next start"
              " resumes a run left PURGING and reconciles when its purge ends"]),
        ):
            with self.subTest(case=name):
                self.reset()
                if nexts:
                    self.set_next(*nexts)
                self.set_purge(answer)
                self.archive()
                self.assertExit(code)
                for text in texts:
                    self.assertIn(text, self.result.stderr)
                self.assertNotIn("reconcile", self.order())
                self.assertFalse(self.incident.exists())
                self.assertEqual(self.result.stdout, "")

    # AR-35: an interruption during the purge says that the next start resumes the purge; one during the reconcile
    # after a purge leaves the incident record.
    def test_ar35_interrupted(self):
        self.set_purge({**purge_out(), "signal": "TERM"})
        self.archive()
        self.assertExit(2)
        self.assertIn(f"interrupted during the purge of run {RUN}. Whole components only are ever deleted: the next start"
                      " resumes the purge of a run left PURGING", self.result.stderr)
        self.assertFalse(self.lock.exists())
        self.reset()
        self.set_rule(RECONCILE, {**out(reconcile_doc(0)), "signal": "TERM"})
        self.archive()
        self.assertExit(2)
        self.assertIn(f"interrupted before the reconcile after the purge of run {RUN} ended: run it by hand",
                      self.result.stderr)
        self.assertEqual(self.incident_values()["result"], "interrupted")
        self.reset()
        self.rules["backup.sh"] = [rule(r".*", {**backup_line(), "signal": "TERM"})]
        self.archive()
        self.assertExit(2)
        self.assertIn(f"interrupted during the pre-purge backup of run {RUN}. Nothing was deleted", self.result.stderr)

    # AR-36: recover logs the purge receipts it copied into the copy folder and the ones it could not copy.
    def test_ar36_recover_receipts(self):
        copied = f"/archive-copy/{ARCHIVE_NAME}.purged.json"
        problem = "The purge receipt could not be copied into /archive-copy: [Errno 28] No space left on device"
        self.set_job("recover", job_out("recover", "recovered", failed_run_ids=[], removed_partial=[], kept_partial=[],
                                        copied_receipts=[copied], receipt_problems=[problem]))
        self.set_next(("none",))
        self.archive()
        self.assertExit(0)
        self.assertIn(f"copied the purge receipt {copied} into the copy folder", self.result.stderr)
        self.assertIn(f"a purge receipt was not copied: {problem} (the next start tries again; status reports"
                      " receipt_not_copied meanwhile", self.result.stderr)
        self.assertEqual(len(self.record_dirs()), 1)

    # AR-37: `next` naming a step again for the same run in one start (a step made no progress) stops the start, also
    # when another step came in between: each step runs at most once per run and start, so the loop always ends.
    def test_ar37_no_progress(self):
        for name, answers, steps, again in (
            ("the same step twice", (("copy", "RUNNING"), ("copy", "RUNNING")), [f"copy --run {RUN}"], "copy"),
            ("a step again after another", (("export", "RUNNING"), ("verify", "RUNNING"), ("export", "RUNNING")),
             [f"export --run {RUN}", "next", f"verify --run {RUN}"], "export"),
        ):
            with self.subTest(case=name):
                self.reset()
                self.set_next(*answers)
                self.archive()
                self.assertExit(2)
                self.assertEqual(self.order(), ["recover", "evaluate", "next", *steps, "next", "status"])
                self.assertIn(f"history-archive next answered {again} again for run {RUN}: a step of this start made no"
                              " progress", self.result.stderr)
                self.assertEqual(self.backup_calls(), [])

    # AR-38: while the incident record of a reconcile after a purge exists, a start fails closed: recover and status
    # only (`next` is never asked), exit 1 with the way out (OPERATIONS_RUNBOOK §8), the record kept; a run left
    # PURGING is not resumed either. Once the operator removes the record, the next start goes on (here: the resumed
    # purge and its reconcile). When recover fails a run in that start (a database restored from its pre-purge backup),
    # the exit is 3.
    def test_ar38_incident_open_fails_closed(self):
        self.write_incident(self.incident)
        self.set_next(("purge", "PURGING"))
        self.set_purge(purge_out(resumed=True))
        self.archive()
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertEqual(self.result.stdout, "")
        self.assertIn("archive: refused — the incident record ", self.result.stderr)
        self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
        self.assertEqual(self.backup_calls(), [])
        self.assertTrue(self.incident.exists())
        self.assertFalse(self.lock.exists())
        # The operator removes the record after the investigation: the next start resumes the purge.
        self.incident.unlink()
        self.reset_calls()
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"purge --run {RUN}", "reconcile", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertNotIn("refused", self.result.stderr)
        self.assertFalse(self.incident.exists())
        with self.subTest(case="recover fails a run restored from its pre-purge backup"):
            self.reset()
            self.write_incident(self.incident)
            self.set_job("recover", job_out("recover", "recovered", failed_run_ids=[RUN], removed_partial=[],
                                            kept_partial=[], copied_receipts=[], receipt_problems=[]))
            self.archive()
            self.assertExit(3)
            self.assertEqual(self.order(), ["recover", "status"])
            self.assertIn(f"run(s) {RUN} stopped while RUNNING and are FAILED now", self.result.stderr)
            self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
            self.assertTrue(self.incident.exists())

    # AR-39: the same through `pf archive` (a manual start with the default records folder $HOME/partflow-deployments)
    # and at a scheduled start, which reports the refusal once for its hour.
    def test_ar39_incident_open_pf_archive_and_scheduled(self):
        records = self.tmp / "partflow-deployments"
        incident = records / "archive" / "reconcile-after-purge"
        self.write_incident(incident)
        self.run_script(PF, "archive")
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
        self.assertTrue(incident.exists())
        self.assertFalse((records / ".archive.lock").exists())
        # A scheduled start that is due (the records folder of --records-dir).
        self.reset_calls()
        self.write_incident(self.incident)
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.assertTrue(self.incident.exists())
        # The next 5-minute start of that hour ends at once, without Docker (no deletion is carried out while the
        # record exists, so the cheap check does not run either): the refusal is reported once per hour.
        self.reset_calls()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertEqual(self.calls("docker"), [])


# ---------------------------------------------------------------------------
# AR-43..AR-54: build step 3, permanent deletion requests
# ---------------------------------------------------------------------------


class Deletion(ArchiveHarness):
    def set_delete(self, answer):
        self.set_rule(MAINT + f"delete --request {REQ} .*$", answer)

    # AR-43: the cheap check at a start within the hour of its last slot. No request open: NOT_DUE after the check
    # alone. A request open: this start carries it out at once and prints the DELETION line. No answer (the backend is
    # down): NOT_DUE, quietly; the request waits for a later start.
    def test_ar43_cheap_check_within_the_hour(self):
        self.write_stamp(epoch_minute(12, 20))
        self.at(12, 35)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertTrue(self.cheap_only())
        self.assertEqual(self.record_dirs(), [])
        self.reset_calls()
        self.set_deletion()
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), DELETION_ORDER)
        self.assertEqual(self.result.stdout, f"DELETION deleted {REQ} COMPLETED\n")
        self.assertIn(f"deletion request {REQ} (APPROVED) is open: this start carries it out now and does no run work.",
                      self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.assertEqual(self.backup_calls(), [DEL_BACKUP_ARGUMENTS])
        with self.subTest(case="the check gives no answer"):
            self.reset()
            self.write_stamp(epoch_minute(12, 20))
            self.at(12, 35)
            self.set_deletion()
            self.set_rule(CHEAP, out("", rc=1, stderr='service "backend" is not running\n'))
            self.archive("--scheduled")
            self.assertEqual((self.result.returncode, self.result.stdout, self.result.stderr), (0, "NOT_DUE\n", ""))
            self.assertTrue(self.cheap_only())
        with self.subTest(case="a release runs, or archival is not set up: quiet, before the check"):
            for case in ("release", "archive dir"):
                self.reset()
                self.write_stamp(epoch_minute(12, 20))
                self.at(12, 35)
                self.set_deletion()
                if case == "release":
                    (self.records / ".release.lock").mkdir(parents=True)
                else:
                    self.write_env([line for line in self.env_lines if not line.startswith("PARTFLOW_ARCHIVE_DIR=")])
                self.archive("--scheduled")
                self.assertEqual((self.result.returncode, self.result.stdout, self.result.stderr), (0, "NOT_DUE\n", ""))
                self.assertEqual(self.calls("docker"), [])

    # AR-44: a request found at the Start minute is carried out first: recover → evaluate (step 4: the start is due for
    # the hourly work) → next → start-deletion → pre-purge backup → delete → reconcile → status, under the archive lock,
    # with no run work, and the slot is left to the next start, which does the hourly work, evaluate included. A manual
    # start (pf archive) runs no cheap check and works on the request first too.
    def test_ar44_deletion_order_and_the_slot(self):
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.set_deletion()
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), DELETION_ORDER_DUE)
        self.assertEqual(self.result.stdout, f"DELETION deleted {REQ} COMPLETED\n")
        self.assertEqual(self.stamp_value(), epoch_minute(11, 20))
        owner = self.lock_during("recover")
        for held in (self.lock_during(f"start-deletion --request {REQ}"), self.lock_at("backup.sh"),
                     self.lock_during(DELETE_WITH_BACKUP), self.lock_at("docker", RECONCILE)):
            self.assertEqual(held, owner)
        self.assertIsNone(self.docker_entries(CHEAP)[0]["watch"][str(self.lock / "owner")])
        self.assertFalse(self.lock.exists())
        record = self.record_dirs()[0]
        for name in ("check.json", "recover.json", "next.json", "start-deletion.json", "next-2.json", "backup.out",
                     "delete.json", "reconcile.json", "status.json", "messages.log"):
            self.assertTrue((record / name).is_file(), name)
        self.assertIn(f"deletion request {REQ}: the pre-purge backup {DEL_BACKUP} is published and verified (its dump"
                      f" started at {DEL_DUMP}).", self.result.stderr)
        self.assertIn(f"deletion request {REQ}: deleted (audit_events 4, part_movements 12, quantity_flows 2,"
                      " assigned_route_steps 6, assigned_routes 2, work_order_demands 1, work_orders 1) in one"
                      f" transaction, after its pre-purge backup {DEL_BACKUP}.", self.result.stderr)
        self.assertIn(f"the reconcile after the deletion of request {REQ} is clean", self.result.stderr)
        self.assertFalse(self.incident.exists())
        # 12:25: the request is done; the 12:20 slot is still due, so this start does the hourly work.
        self.rules["docker"] = default_jobs()
        self.rules["backup.sh"] = [rule(r".*", backup_line())]
        self.reset_calls()
        (self.state / "counts.json").unlink()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "next", f"copy --run {RUN}", "next", "backup.sh",
                                        PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="a manual start"):
            self.reset()
            self.set_deletion()
            self.archive()
            self.assertExit(0)
            self.assertEqual(self.order(), DELETION_ORDER_DUE)
            self.assertEqual(self.docker_entries(CHEAP), [])
            self.assertEqual(self.result.stdout, f"DELETION deleted {REQ} COMPLETED\n")
            self.assertIsNone(self.stamp_value())

    # AR-45: start-deletion fails the request (the requester re-check, exit 3), is refused (exit 1) or gives no valid
    # report (exit 2; also a report of another request): no backup, no deletion, no reconcile, no DELETION line.
    def test_ar45_start_deletion_exits(self):
        reason = ("The User who requested deletion request 7, Ann (user 1), is no longer active, so nothing was deleted."
                  " A holder of Delete Work Orders and Part Numbers permanently may request the deletion again.")
        refused = "Deletion request 7 is CANCELLED; only an APPROVED request can be started. Nothing was changed."
        for name, answer, code, text in (
            ("requester", start_deletion_out("failed", 3, ("run_failed", reason)), 3,
             f"archive: deletion request {REQ} FAILED: {reason}"),
            ("refused", start_deletion_out("refused", 1, ("wrong_state", refused)), 1,
             f"history-archive start-deletion refused: {refused}"),
            ("no report", out("", rc=137), 2,
             f"the outcome of the start of deletion request {REQ} is unknown. Nothing was deleted: the next start makes"
             " the request FAILED if it was started (recover), or starts it again."),
            # A report that names another request is no valid report of this step.
            ("another request", job_out("start-deletion", "started", request_id=8, state="RUNNING",
                                        request=request_object("RUNNING", request_id=8)), 2,
             "history-archive start-deletion gave no valid report (exit 0;"),
        ):
            with self.subTest(case=name):
                self.reset()
                self.set_deletion()
                self.set_job(f"start-deletion --request {REQ}", answer)
                self.archive()
                self.assertExit(code)
                self.assertEqual(self.order(), ["recover", "evaluate", "next", f"start-deletion --request {REQ}",
                                                "status"])
                self.assertIn(text, self.result.stderr)
                self.assertEqual((self.result.stdout, self.backup_calls()), ("", []))
                self.assertFalse(self.incident.exists())

    # AR-46: the deletion's pre-purge backup fails (or names another backup): nothing is deleted, exit 2, and the
    # request stays RUNNING for the next start's recover.
    def test_ar46_deletion_backup_failure(self):
        for name, answer, why in (
            ("dump failed", out("", rc=3, stderr="backup: pg_dump failed (exit 1). No backup was published.\n"),
             "the pre-purge backup failed (backup.sh exit 3;"),
            ("another label", backup_line(name=f"20261008T122032Z-pre-purge-{ARCHIVE_NAME}", started=DEL_DUMP),
             "backup.sh did not name the pre-purge backup with its dump start"),
        ):
            with self.subTest(case=name):
                self.reset()
                self.set_deletion()
                self.rules["backup.sh"] = [rule(r".*", answer)]
                self.archive()
                self.assertExit(2)
                self.assertEqual(self.order(), ["recover", "evaluate", "next", f"start-deletion --request {REQ}", "next",
                                                "backup.sh", "status"])
                self.assertIn(f"archive: deletion request {REQ}: {why}", self.result.stderr)
                self.assertIn("Nothing was deleted. The request stays RUNNING, and the next start makes it FAILED"
                              f" (recover). {REQUEST_AGAIN}", self.result.stderr)
                self.assertEqual(self.result.stdout, "")
                self.assertFalse(self.incident.exists())

    # AR-47: every exit of delete. Exit 3: FAILED, nothing deleted, no reconcile. Exit 1: refused. Every exit 2
    # (`interrupted`, `outcome_unknown`, any other) and a missing report: status tells (review finding 6: even an
    # `interrupted` report could follow a COMMIT that succeeded). COMPLETED → the reconcile runs and the start ends as
    # deleted; still RUNNING → nothing deleted, no reconcile, exit 2; FAILED → nothing deleted, exit 3; status without
    # an answer → the reconcile runs anyway.
    def test_ar47_delete_exits(self):
        changed = ("The data changed after the request; nothing was deleted. Request it again. Changed rows:"
                   " part_movements +1 (13).")
        interrupted = ("Deletion request 7 stopped on a database error that does not last (OperationalError). It stays"
                       " RUNNING: its one transaction was rolled back, so nothing was deleted, and the next start of the"
                       " job makes it FAILED. Request the deletion again.")
        unknown = ("The database connection failed while committing deletion request 7: it is COMPLETED (deleted) or"
                   " still RUNNING (nothing deleted; the next start makes it FAILED). Run history-archive status to see"
                   " it.")
        invalid = "The backup is not the pre-purge backup of deletion request 7. Nothing was changed."
        unavailable = "PartFlow could not reach its database. Nothing was changed."
        completed = request_object("COMPLETED")
        running = {**request_object("RUNNING"), "age_seconds": 30}
        failed = request_object("FAILED", failure_reason="The deletion deleted 2 rows of work_orders, but the request"
                                                         " stored 1; nothing was deleted. Request it again.")
        still_running = (f"status shows deletion request {REQ} still RUNNING: its deletion did not commit, so nothing"
                         " was deleted, and the next start makes it FAILED (recover).")
        cases = (
            ("failed", delete_out("failed", 3, "FAILED", ("run_failed", changed)), None, 3,
             [f"archive: deletion request {REQ} FAILED: {changed} Nothing was deleted: the deletion is one"
              f" transaction. {REQUEST_AGAIN}"], False, ""),
            ("interrupted, RUNNING", delete_out("interrupted", 2, "RUNNING", ("deletion_interrupted", interrupted)),
             deletion_status(open_request=running), 2,
             [f"history-archive delete ended with exit 2 (deletion_interrupted): {interrupted}", still_running], False,
             ""),
            ("interrupted, COMPLETED", delete_out("interrupted", 2, "RUNNING", ("deletion_interrupted", interrupted)),
             deletion_status(last_request=completed), 0,
             [f"status shows deletion request {REQ} COMPLETED: its deletion committed", "is clean"], True,
             f"DELETION deleted {REQ} COMPLETED\n"),
            ("could not run, RUNNING", delete_out("could_not_run", 2, "RUNNING", ("database_unavailable", unavailable)),
             deletion_status(open_request=running), 2,
             [f"history-archive delete ended with exit 2 (database_unavailable): {unavailable}", still_running], False,
             ""),
            ("no report, FAILED", out("", rc=137), deletion_status(last_request=failed), 3,
             [f"status shows deletion request {REQ} FAILED: The deletion deleted 2 rows of work_orders, but the request"
              " stored 1; nothing was deleted. Request it again. Nothing was deleted: the deletion is one transaction."],
             False, ""),
            ("refused", delete_out("refused", 1, "RUNNING", ("backup_invalid", invalid)), None, 1,
             [f"history-archive delete refused: {invalid}", f"Nothing was deleted. Deletion request {REQ} stays RUNNING,"
              " and the next start makes it FAILED (recover)."], False, ""),
            ("unknown, COMPLETED", delete_out("could_not_run", 2, None, ("outcome_unknown", unknown)),
             deletion_status(last_request=completed), 0,
             [f"status shows deletion request {REQ} COMPLETED: its deletion committed, after its pre-purge backup"
              f" {DEL_BACKUP}.", f"the reconcile after the deletion of request {REQ} is clean"], True,
             f"DELETION deleted {REQ} COMPLETED\n"),
            ("unknown, RUNNING", delete_out("could_not_run", 2, None, ("outcome_unknown", unknown)),
             deletion_status(open_request=running), 2,
             [f"status shows deletion request {REQ} still RUNNING: its deletion did not commit, so nothing was deleted,"
              " and the next start makes it FAILED (recover)."], False, ""),
            ("no report, no status", out("", rc=137), out("", rc=2), 2,
             [f"history-archive status does not tell whether the deletion of request {REQ} committed: the reconcile"
              " runs now, as after a deletion."], True, ""),
        )
        for name, answer, status, code, texts, reconciled, stdout in cases:
            with self.subTest(case=name):
                self.reset()
                self.set_deletion()
                self.set_delete(answer)
                if status is not None:
                    self.set_job("status", status, status_ok())
                self.archive()
                self.assertExit(code)
                for text in texts:
                    self.assertIn(text, self.result.stderr)
                expected = ["recover", "evaluate", "next", f"start-deletion --request {REQ}", "next", "backup.sh",
                            DELETE_WITH_BACKUP]
                expected += (["status"] if status is not None else []) + (["reconcile"] if reconciled else []) + ["status"]
                self.assertEqual(self.order(), expected)
                self.assertEqual(self.result.stdout, stdout)
                self.assertFalse(self.incident.exists())

    # AR-48: a reconcile finding after a deletion is the incident of a reconcile after a purge: exit 4, the same record
    # file with the request and its pre-purge backup; while it exists no start carries out a deletion (manual: recover
    # and status only; scheduled within the hour: NOT_DUE without Docker).
    def test_ar48_reconcile_finding_after_a_deletion(self):
        self.set_deletion()
        self.set_rule(RECONCILE, out(reconcile_doc(1, failing=("c",)), rc=1))
        self.archive()
        self.assertExit(4)
        self.assertEqual(self.result.stdout, "")
        record = self.record_dirs()[0]
        self.assertIn(f"archive: INCIDENT — the reconcile after the deletion of request {REQ} found a mismatch (checks c):"
                      f" {record.as_posix()}/reconcile.json. Stop requesting runs and deletions and follow"
                      " OPERATIONS_RUNBOOK §8 (Reconcile finding after a deletion): the pre-purge backup of the deletion"
                      f" is {DEL_BACKUP};", self.result.stderr.replace(sh_path(record), record.as_posix()))
        values = self.incident_values()
        self.assertEqual({k: values[k] for k in ("result", "exit_code", "run", "archive_name", "request",
                                                 "pre_purge_backup", "failing_checks")},
                         {"result": "mismatch", "exit_code": "1", "run": "", "archive_name": "", "request": str(REQ),
                          "pre_purge_backup": DEL_BACKUP, "failing_checks": "c"})
        # A later request: a manual start runs recover and status only.
        self.reset_calls()
        self.set_rule(RECONCILE, out(reconcile_doc(0)))
        self.at(13, 20)
        self.archive()
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertIn(f" of a reconcile after a deletion exists (deletion request {REQ}, result mismatch): no run,"
                      " deletion request or view is worked on while it exists", self.result.stderr)
        self.assertIn("Follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a deletion);", self.result.stderr)
        # A scheduled start within the hour does not even look for the request.
        self.reset_calls()
        self.write_stamp(epoch_minute(13, 20))
        self.at(13, 35)
        self.archive("--scheduled")
        self.assertEqual((self.result.returncode, self.result.stdout), (0, "NOT_DUE\n"))
        self.assertEqual(self.calls("docker"), [])
        self.assertTrue(self.incident.exists())

    # AR-49: an APPROVED request that arrives while this start's run is RUNNING waits (contract B3 §3): `next` answers
    # the run's steps with the request named, and the run goes on to its purge; a later start takes the request.
    def test_ar49_request_waits_while_a_run_is_running(self):
        waiting = request_object("APPROVED")
        self.set_next(("export", "APPROVED"), ("verify", "RUNNING", 0, None, waiting),
                      ("copy", "RUNNING", 0, None, waiting), ("backup", "RUNNING", 0, None, waiting))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "next", f"copy --run {RUN}", "next", "backup.sh",
                                        PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        message = f"deletion request {REQ} waits while run {RUN} is RUNNING in this start: a later start carries it out."
        self.assertEqual(self.result.stderr.count(message), 1)

    # AR-50: recover found a deletion request left RUNNING (an interrupted start, a failed pre-purge backup, a database
    # restored from that backup): it is FAILED (exit 3), nothing was deleted, and the work goes on.
    def test_ar50_recover_fails_a_request_left_running(self):
        self.set_job("recover", job_out("recover", "recovered", failed_run_ids=[], failed_request_ids=[REQ],
                                        removed_partial=[], kept_partial=[], copied_receipts=[], receipt_problems=[]))
        self.set_next(("none",))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
        self.assertIn(f"deletion request(s) {REQ} stopped while RUNNING and are FAILED now (an interrupted job, a"
                      " pre-purge backup that failed, a deletion that stopped on a database error, or a database restored"
                      " from the pre-purge backup of a deletion). Nothing was deleted by them in this database."
                      f" {REQUEST_AGAIN}", self.result.stderr)

    # AR-51: an interruption during the deletion fails closed (its transaction may have committed without a
    # reconcile: the incident record); one during its pre-purge backup deleted nothing.
    def test_ar51_interrupted_deletion(self):
        self.set_deletion()
        self.set_delete({**delete_out(), "signal": "TERM"})
        self.archive()
        self.assertExit(2)
        self.assertIn(f"interrupted during the deletion of request {REQ}: its one transaction either committed",
                      self.result.stderr)
        values = self.incident_values()
        self.assertEqual((values["result"], values["request"], values["pre_purge_backup"]),
                         ("interrupted", str(REQ), DEL_BACKUP))
        self.assertFalse(self.lock.exists())
        self.reset()
        self.set_deletion()
        self.rules["backup.sh"] = [rule(r".*", {**backup_line(name=DEL_BACKUP, started=DEL_DUMP), "signal": "TERM"})]
        self.archive()
        self.assertExit(2)
        self.assertIn(f"interrupted during the pre-purge backup of deletion request {REQ}. Nothing was deleted",
                      self.result.stderr)
        self.assertFalse(self.incident.exists())

    # AR-52: a scheduled start that finds a request while another archive.sh holds the lock defers it (exit 0), and a
    # start taken for a request that is no longer open (cancelled meanwhile), or that waits for a run RUNNING in another
    # history-archive command, works on no run when the hourly work is not due.
    def test_ar52_lock_held_and_request_gone(self):
        self.write_lock()
        self.write_stamp(epoch_minute(12, 20))
        self.at(12, 35)
        self.set_deletion()
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "DEFERRED archive_running\n")
        self.assertIn("the open deletion request waits for a later start", self.result.stderr)
        self.assertEqual(self.steps(), [])
        self.reset()
        self.write_stamp(epoch_minute(12, 20))
        self.at(12, 35)
        self.set_deletion()
        self.set_next(("export", "APPROVED"))
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "next", "status"])
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertIn(f"deletion request {REQ} is no longer open (it was cancelled or finished meanwhile), and the hourly"
                      " work is not due: this start ends.", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="the request waits for a run RUNNING in another history-archive command"):
            self.reset()
            self.write_stamp(epoch_minute(12, 20))
            self.at(12, 35)
            self.set_deletion()
            self.set_rule(MAINT + "next$", next_out("running", "RUNNING", request=request_object("APPROVED")))
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "next", "status"])
            self.assertEqual(self.result.stdout, "NOT_DUE\n")
            self.assertIn(f"deletion request {REQ} waits while run {RUN} is RUNNING in another history-archive command,"
                          " and the hourly work is not due: this start ends.", self.result.stderr)
            self.assertEqual(self.stamp_value(), epoch_minute(12, 20))

    # AR-53: help names the deletion requests, the DELETION line and the exit codes of step 3.
    def test_ar53_help(self):
        result = self.archive("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("A permanent deletion request comes first", "DELETION <outcome> <request id> <state>",
                     "3 a run or a deletion request failed", "the reconcile after a purge or a deletion"):
            self.assertIn(text, result.stdout)
        header = ARCHIVE_SH.read_text(encoding="utf-8").split("\nset -eu\n", 1)[0]
        self.assertIn("`DELETION <outcome> <request id> <state>`", header)


# ---------------------------------------------------------------------------
# AR-58..AR-63: build step 4, automatic runs (AR-64, the check.sh lines, is in CheckArchival)
# ---------------------------------------------------------------------------

PROPOSED_RUN = run_object("PROPOSED", trigger="AUTOMATIC", reason=AUTOMATIC_REASON)
# The whole hourly work on run 3, from its export to the reconcile after its purge.
RUN_WORK = ["next", f"export --run {RUN}", "next", f"verify --run {RUN}", "next", f"copy --run {RUN}", "next",
            "backup.sh", PURGE_WITH_BACKUP, "reconcile", "status"]


class AutomaticRuns(ArchiveHarness):
    def set_evaluate(self, answer):
        self.set_job("evaluate", answer)

    # AR-58: the hourly work evaluates the automatic runs after recover and before the `next` loop (contract B4 §3.2).
    # Every decision exits 0: its result and message are logged (a created run with its state), and a start that then
    # works on no run prints EVALUATE <result> <run> <state>, the open run that `next` named. Only a created run keeps the
    # start's reports.
    def test_ar58_each_decision_of_evaluate(self):
        for result in ("off", "no_retention_period", "not_due", "authorization_invalid", "below_limit",
                       "nothing_eligible", "settings_changed"):
            with self.subTest(result=result):
                self.reset()
                self.set_evaluate(evaluate_out(result))
                self.set_next(("none",))
                self.archive()
                self.assertExit(0)
                self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
                self.assertEqual(self.result.stdout, f"EVALUATE {result} - -\n")
                self.assertIn(f"archive: history-archive evaluate: {result}\n", self.result.stderr)
                self.assertIn(f"archive: evaluate: {result}. {EVALUATE_MESSAGES[result]}\n", self.result.stderr)
                self.assertEqual(self.record_dirs(), [])
                self.assertFalse(self.lock.exists())
        with self.subTest(result="run_open"):
            self.reset()
            self.set_evaluate(evaluate_out("run_open", run_id=RUN, state="WAITING_FOR_COPY"))
            self.set_next(("wait", "WAITING_FOR_COPY"))
            self.archive()
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
            self.assertEqual(self.result.stdout, f"EVALUATE run_open {RUN} WAITING_FOR_COPY\n")
            self.assertIn(f"archive: evaluate: run_open, run {RUN} WAITING_FOR_COPY. {EVALUATE_MESSAGES['run_open']}\n",
                          self.result.stderr)
            self.assertIn(f"run {RUN} waits for its off-host copy to be confirmed", self.result.stderr)
            self.assertEqual(self.record_dirs(), [])
        with self.subTest(result="created, PROPOSED"):
            self.reset()
            self.set_evaluate(evaluate_out("created", run_id=RUN, state="PROPOSED"))
            self.set_rule(MAINT + "next$", next_out("wait", "PROPOSED", run=PROPOSED_RUN))
            self.archive()
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
            self.assertEqual(self.result.stdout, f"EVALUATE created {RUN} PROPOSED\n")
            self.assertIn(f"archive: history-archive evaluate: run {RUN} created (PROPOSED)\n", self.result.stderr)
            self.assertIn(f"archive: evaluate: created, run {RUN} PROPOSED. {EVALUATE_MESSAGES['created']}\n",
                          self.result.stderr)
            records = self.record_dirs()
            self.assertEqual(len(records), 1)
            for name in ("evaluate.json", "evaluate.log", "next.json", "status.json", "messages.log"):
                self.assertTrue((records[0] / name).is_file(), name)
            self.assertEqual(self.backup_calls(), [])

    # AR-59: a run created APPROVED under the standing authorization is worked on in the same start, like a requested
    # run: export, verify, copy, the pre-purge backup, the purge and the reconcile after it; the final line is ARCHIVE.
    def test_ar59_approved_automatic_run_worked_on_in_the_same_start(self):
        self.set_evaluate(evaluate_out("created", run_id=RUN, state="APPROVED"))
        self.set_rule(MAINT + "next$", *[next_out(action, state, run=automatic_run(state)) for action, state in HAPPY])
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", *RUN_WORK])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertIn(f"archive: evaluate: created, run {RUN} APPROVED. {CREATED_APPROVED}\n", self.result.stderr)
        self.assertEqual(self.backup_calls(), [BACKUP_ARGUMENTS + ["60"]])
        self.assertIn(f"the reconcile after the purge of run {RUN} is clean", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        records = self.record_dirs()
        self.assertEqual(len(records), 1)
        self.assertTrue((records[0] / "evaluate.json").is_file())

    # AR-60: a PROPOSED run waits for a person: `next` answers wait, the start works on no run and says who may approve
    # or dismiss it, no lock and no report stay, and the slot is attempted (the next start within the hour runs nothing).
    def test_ar60_proposed_run_waits(self):
        self.set_evaluate(evaluate_out("run_open", run_id=RUN, state="PROPOSED", message=(
            f"Run {RUN} is still open (PROPOSED); no automatic run is created while a run is open.")))
        self.set_rule(MAINT + "next$", next_out("wait", "PROPOSED", run=PROPOSED_RUN))
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
        self.assertEqual(self.result.stdout, f"EVALUATE run_open {RUN} PROPOSED\n")
        self.assertIn(f"archive: run {RUN} is PROPOSED: it waits for a holder of Run history archival and purge to"
                      " approve or dismiss it in Administration → History archival & purge (OPERATIONS_RUNBOOK §11).",
                      self.result.stderr)
        self.assertNotIn("off-host copy", self.result.stderr)
        self.assertEqual(self.backup_calls(), [])
        self.assertEqual(self.record_dirs(), [])
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.reset_calls()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertTrue(self.cheap_only())

    # AR-61: evaluate refused (exit 1) or unable to run (exit 2, also a time limit), or without a valid report: the
    # error is logged, and the `next` loop goes on with the open run (here a requested run, through its purge and the
    # reconcile after it). The start's exit status reports it (no final line), unless recover set one already; a
    # scheduled start reports it once for its hour.
    def test_ar61_evaluate_refused_or_failing_goes_on(self):
        refused = "Another history-archive command is running. Nothing was changed."
        cases = (
            ("refused", evaluate_out(rc=1, error=("job_running", refused)), 1,
             [f"archive: history-archive evaluate refused: {refused}"]),
            ("timed out", evaluate_out(rc=2, error=("evaluation_timed_out", TIMED_OUT)), 2,
             [f"archive: history-archive evaluate could not run (exit 2): {TIMED_OUT}"]),
            ("no report", out("", rc=137, stderr="Error response from daemon: container killed\n"), 2,
             ["archive: history-archive evaluate gave no valid report (exit 137;",
              "archive: the outcome of evaluate is unknown: it may have created an automatic run, which next then"
              " names; nothing was deleted."]),
        )
        for name, answer, code, texts in cases:
            with self.subTest(case=name):
                self.reset()
                self.set_evaluate(answer)
                self.archive()
                self.assertExit(code)
                self.assertEqual(self.order(), ["recover", "evaluate", *RUN_WORK])
                self.assertEqual(self.result.stdout, "")
                for text in texts:
                    self.assertIn(text, self.result.stderr)
                self.assertIn("archive: the work goes on (the next loop still works on an open run); the next hourly"
                              " work evaluates the automatic runs again (OPERATIONS_RUNBOOK §11).", self.result.stderr)
                self.assertIn(f"the reconcile after the purge of run {RUN} is clean", self.result.stderr)
                self.assertEqual(len(self.record_dirs()), 1)
        with self.subTest(case="the exit status of recover is kept"):
            self.reset()
            self.set_job("recover", job_out("recover", "recovered", failed_run_ids=[2], removed_partial=[],
                                            kept_partial=[], copied_receipts=[], receipt_problems=[]))
            self.set_evaluate(evaluate_out(rc=2, error=("evaluation_timed_out", TIMED_OUT)))
            self.set_next(("none",))
            self.archive()
            self.assertExit(3)
            self.assertEqual(self.order(), ["recover", "evaluate", "next", "status"])
        with self.subTest(case="a scheduled start reports it once for its hour"):
            self.reset()
            self.set_evaluate(evaluate_out(rc=2, error=("evaluation_timed_out", TIMED_OUT)))
            self.set_next(("none",))
            self.write_stamp(epoch_minute(11, 20))
            self.at(12, 20)
            self.archive("--scheduled")
            self.assertExit(2)
            self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
            self.reset_calls()
            self.at(12, 25)
            self.archive("--scheduled")
            self.assertEqual((self.result.returncode, self.result.stdout, self.result.stderr), (0, "NOT_DUE\n", ""))
            self.assertTrue(self.cheap_only())

    # AR-62: no evaluate in a start taken only for a deletion request (a scheduled start within the hour of its slot, or
    # the first scheduled start), and none while the incident record exists (manual or scheduled, also at the slot).
    def test_ar62_no_evaluate_for_a_deletion_alone_or_during_an_incident(self):
        for name, stamp in (("within the hour", epoch_minute(12, 20)), ("first scheduled start", None)):
            with self.subTest(case=name):
                self.reset()
                if stamp is not None:
                    self.write_stamp(stamp)
                self.at(12, 35)
                self.set_deletion()
                self.set_evaluate(evaluate_out("created", run_id=RUN, state="APPROVED"))
                self.archive("--scheduled")
                self.assertExit(0)
                self.assertEqual(self.order(), DELETION_ORDER)
                self.assertNotIn("evaluate", self.steps())
                self.assertEqual(self.result.stdout, f"DELETION deleted {REQ} COMPLETED\n")
        for name, arguments in (("manual", ()), ("scheduled at the slot", ("--scheduled",))):
            with self.subTest(case=f"incident record, {name}"):
                self.reset()
                self.write_incident(self.incident)
                self.set_evaluate(evaluate_out("created", run_id=RUN, state="APPROVED"))
                self.write_stamp(epoch_minute(11, 20))
                self.at(12, 20)
                self.archive(*arguments)
                self.assertExit(1)
                self.assertEqual(self.order(), ["recover", "status"])
                self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
                self.assertEqual(self.result.stdout, "")

    # AR-63: the help and the header name evaluate, where it runs and where it never runs, and the EVALUATE line.
    def test_ar63_help(self):
        result = self.archive("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("recover, at the hourly work evaluate,", "EVALUATE <result> <run id|-> <state|-> for an hourly work",
                     "EVALUATE off - -, EVALUATE created 7 PROPOSED", "2 could not run (evaluate included)"):
            self.assertIn(text, result.stdout)
        header = ARCHIVE_SH.read_text(encoding="utf-8").split("\nset -eu\n", 1)[0]
        for text in ("`EVALUATE <result> <run id|-> <state|->`", "build steps 1 to 5",
                     "never in a start taken only for a deletion request or for viewing work, and never while the incident"):
            self.assertIn(text, header)


# ---------------------------------------------------------------------------
# AR-65..AR-74: build step 5, viewing an archive (AR-75, the check.sh lines, is in CheckArchival)
# ---------------------------------------------------------------------------

LOAD_VIEW = f"load-view --view {VIEW}"
CLOSE_VIEW = f"close-view --view {CLOSED_VIEW}"
# The step after a load, in a start within the hour of its slot: the viewing work only.
VIEW_ONLY = ["recover", "next", LOAD_VIEW, "next", "status"]
LOADED_TEXT = (f"view {VIEW} of run {VIEW_RUN} is OPEN: 4190 Movements, 305 flows, 12 lineage edges, 40 Assigned Routes"
               f" and 97 names loaded from the archive folder {VIEW_ARCHIVE}; it stays open until"
               " 2026-10-15T12:21:03.000000Z.")


class Viewing(ArchiveHarness):
    def set_views(self, views=((VIEW, "REQUESTED", "load_view"),), after=(("none",),), nexts=None):
        """Viewing work (contract B5 §3.1, §3.2): the cheap check lists VIEWS as (view id, state, action); `next` hands
        out NEXTS (by default the close_view and load_view of VIEWS, in their order), then the run answers AFTER
        (next_out arguments); load-view loads view 4 and close-view closes view 3."""
        self.set_rule(CHEAP, settings_out(views=views))
        if nexts is None:
            nexts = [next_view_out(action, state, view_id) for view_id, state, action in views if action != "recover"]
        self.set_rule(MAINT + "next$", *nexts, *[next_out(*answer) for answer in after])
        self.set_job(LOAD_VIEW, load_view_out())
        self.set_job(CLOSE_VIEW, close_view_out())

    def within_the_hour(self):
        """A scheduled start at 12:35, within the hour of its 12:20 slot."""
        self.write_stamp(epoch_minute(12, 20))
        self.at(12, 35)

    def at_the_slot(self):
        """A scheduled start at 12:20, the Start minute: the hourly work is due."""
        self.write_stamp(epoch_minute(11, 20))
        self.at(12, 20)

    def restart(self):
        """The calls and the answer counts of the fakes start again (the rules of a later start)."""
        self.reset_calls()
        counts = self.state / "counts.json"
        if counts.exists():
            counts.unlink()

    # AR-65 (lead decision 1): the cheap check finds viewing work, so a start within the hour does it at once: recover,
    # next, load-view, next, status, under the archive lock (release.sh refuses meanwhile), with no evaluate and no run
    # work; the final line is VIEW; the hourly slot stays as it was; the reports and the check are kept. Once nothing
    # waits, the next start ends after the cheap check (NOT_DUE); without an answer of the check it ends quietly.
    def test_ar65_viewing_work_at_any_start(self):
        self.within_the_hour()
        self.set_views()
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), VIEW_ONLY)
        self.assertEqual(self.result.stdout, f"VIEW loaded {VIEW} OPEN\n")
        self.assertIn(f"archive: viewing work waits for the job (view {VIEW} REQUESTED (load_view)): this start does it"
                      " now.", self.result.stderr)
        self.assertIn(f"archive: history-archive load-view: view {VIEW} loaded (OPEN)\n", self.result.stderr)
        self.assertIn(f"archive: {LOADED_TEXT}\n", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        owner = self.lock_during("recover")
        self.assertIn("by=archive.sh\n", owner)
        self.assertEqual(self.lock_during(LOAD_VIEW), owner)
        self.assertIsNone(self.docker_entries(CHEAP)[0]["watch"][str(self.lock / "owner")])
        self.assertFalse(self.lock.exists())
        record = self.record_dirs()[0]
        for name in ("check.json", "recover.json", "next.json", f"load-view-{VIEW}.json", f"load-view-{VIEW}.log",
                     "next-2.json", "status.json", "messages.log"):
            self.assertTrue((record / name).is_file(), name)
        self.assertEqual(self.backup_calls(), [])
        # 12:40: nothing waits any more.
        self.restart()
        self.set_rule(CHEAP, settings_out())
        self.at(12, 40)
        self.archive("--scheduled")
        self.assertEqual((self.result.returncode, self.result.stdout), (0, "NOT_DUE\n"))
        self.assertTrue(self.cheap_only())
        with self.subTest(case="the check gives no answer"):
            self.reset()
            self.within_the_hour()
            self.set_views()
            self.set_rule(CHEAP, out("", rc=1, stderr='service "backend" is not running\n'))
            self.archive("--scheduled")
            self.assertEqual((self.result.returncode, self.result.stdout, self.result.stderr), (0, "NOT_DUE\n", ""))
            self.assertTrue(self.cheap_only())

    # AR-66 (contract B5 §3.2, §3.6): the order of one start. Deletion work first, then closing before loading, then the
    # run work: a manual start (pf archive) closes view 3, loads view 4, then exports, verifies, copies, backs up and
    # purges run 3 (the final line is ARCHIVE). A start at its slot does the same and marks the slot once its run work
    # begins. A start with a deletion request and a waiting view carries out the deletion only (DELETION; the view waits
    # for the next start). A start within the hour does the viewing work and no run work, also when `next` then names a
    # run step.
    def test_ar66_viewing_work_between_deletion_and_run_work(self):
        both = ((CLOSED_VIEW, "CLOSING", "close_view"), (VIEW, "REQUESTED", "load_view"))
        self.set_views(views=both, after=HAPPY)
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", CLOSE_VIEW, "next", LOAD_VIEW, *RUN_WORK])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertIn(f"view {CLOSED_VIEW} of run {VIEW_RUN} is CLOSED (a person closed it): its loaded rows were removed"
                      " (archive_view_names 97, archive_view_lineage 12, archive_view_flows 305, archive_view_routes 40,"
                      " archive_view_movements 4190).", self.result.stderr)
        self.assertIn(LOADED_TEXT, self.result.stderr)
        record = self.record_dirs()[0]
        for name in (f"close-view-{CLOSED_VIEW}.json", f"load-view-{VIEW}.json", "next-3.json", "export.json"):
            self.assertTrue((record / name).is_file(), name)
        with self.subTest(case="at the slot"):
            self.reset()
            self.at_the_slot()
            self.set_views(views=both, after=HAPPY)
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "evaluate", "next", CLOSE_VIEW, "next", LOAD_VIEW, *RUN_WORK])
            self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
            self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="a deletion request comes first"):
            self.reset()
            self.within_the_hour()
            self.set_deletion()
            self.set_rule(CHEAP, settings_out(pending=True, views=((VIEW, "REQUESTED", "load_view"),)))
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), DELETION_ORDER)
            self.assertEqual(self.result.stdout, f"DELETION deleted {REQ} COMPLETED\n")
            self.assertIn(f"deletion request {REQ} (APPROVED) is open: this start carries it out now", self.result.stderr)
            self.assertIn(f"viewing work waits for the job (view {VIEW} REQUESTED (load_view))", self.result.stderr)
            self.assertNotIn(LOAD_VIEW, self.order())
        with self.subTest(case="within the hour: no run work"):
            self.reset()
            self.within_the_hour()
            self.set_views(after=(("export", "APPROVED"),))
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), VIEW_ONLY)
            self.assertEqual(self.result.stdout, f"VIEW loaded {VIEW} OPEN\n")
            self.assertNotIn("hourly work is not due", self.result.stderr)
            self.assertEqual(self.stamp_value(), epoch_minute(12, 20))

    # AR-67 (lead decision 4): the final line. A start whose hourly work worked on views and on no run prints VIEW (not
    # EVALUATE), naming the last view; a closing by the days says so. Viewing work that comes up while this start's run
    # is RUNNING waits for a later start, which the log says once.
    def test_ar67_the_view_line(self):
        self.at_the_slot()
        self.set_views(views=((CLOSED_VIEW, "OPEN", "close_view"), (VIEW, "REQUESTED", "load_view")),
                       after=(("wait", "WAITING_FOR_COPY"),))
        self.set_job(CLOSE_VIEW, close_view_out(by_person=False))
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", CLOSE_VIEW, "next", LOAD_VIEW, "next", "status"])
        self.assertEqual(self.result.stdout, f"VIEW loaded {VIEW} OPEN\n")
        self.assertIn(f"view {CLOSED_VIEW} of run {VIEW_RUN} is CLOSED (its days ran out)", self.result.stderr)
        self.assertIn(f"run {RUN} waits for its off-host copy to be confirmed", self.result.stderr)
        # The hourly work happened (evaluate, then `next` named the waiting run): the slot is attempted.
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="a close only"):
            self.reset()
            self.within_the_hour()
            self.set_views(views=((CLOSED_VIEW, "CLOSING", "close_view"),))
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "next", CLOSE_VIEW, "next", "status"])
            self.assertEqual(self.result.stdout, f"VIEW closed {CLOSED_VIEW} CLOSED\n")
        with self.subTest(case="a view waits behind this start's run"):
            self.reset()
            waiting = view_object("REQUESTED")
            self.set_next(("export", "APPROVED"), ("verify", "RUNNING", 0, None, None, None, waiting),
                          ("copy", "RUNNING", 0, None, None, None, waiting),
                          ("backup", "RUNNING", 0, None, None, None, waiting))
            self.archive()
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "evaluate", *RUN_WORK])
            self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
            message = (f"view {VIEW} waits while run {RUN} is RUNNING in this start: a later start does its viewing"
                       " work.")
            self.assertEqual(self.result.stderr.count(message), 1)

    # AR-68 (lead decision 3): a failed load ends the start with exit 3: the view is FAILED with its rows removed, and
    # its message says why and what to do; no run work follows, and the hourly slot is left to the next start, which
    # does the hourly work.
    def test_ar68_failed_load(self):
        reason = (f"manifest.json of {VIEW_ARCHIVE} is not the manifest run {VIEW_RUN} verified (its SHA-256 differs)."
                  " Nothing was loaded; restore the archive folder from its off-host copy, then open the run for viewing"
                  " again.")
        self.at_the_slot()
        self.set_views(after=HAPPY)
        self.set_job(LOAD_VIEW, load_view_out("failed", 3, error=("run_failed", reason)))
        self.archive("--scheduled")
        self.assertExit(3)
        self.assertEqual(self.order(), ["recover", "evaluate", "next", LOAD_VIEW, "status"])
        self.assertEqual(self.result.stdout, "")
        self.assertIn(f"archive: view {VIEW} of run {VIEW_RUN} FAILED: {reason} Live history was not changed"
                      " (OPERATIONS_RUNBOOK §11, Viewing an archive).", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(11, 20))
        self.assertEqual(len(self.record_dirs()), 1)
        self.assertFalse(self.lock.exists())
        # 12:25: the view is FAILED, nothing waits: the hourly work, evaluate included.
        self.restart()
        self.set_rule(CHEAP, settings_out())
        self.set_next(*HAPPY)
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "evaluate", *RUN_WORK])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))

    # AR-69 (lead decision 3): a load or a closing that a database error interrupted ends the start with exit 2 (the
    # view stays LOADING, which the next recover fails, or CLOSING, which the next start closes); so does a load that
    # could not run, a step without a valid report (also one that names another view) and a view that `next` hands out
    # again (no progress); a refusal other than wrong_state (AR-76) ends it with exit 1. Nothing follows but status, and
    # the slot stays unmarked.
    def test_ar69_interrupted_refused_or_unknown(self):
        load_stopped = (f"View {VIEW} stopped loading on a database error that does not last (OperationalError). It"
                        " stays LOADING: the next start of the job removes the rows it loaded and makes it FAILED. Open"
                        " the run for viewing again.")
        close_stopped = (f"View {CLOSED_VIEW} stopped closing on a database error that does not last"
                         " (OperationalError). It stays CLOSING: the next start of the job removes the rest of its rows.")
        unknown = (f"View {VIEW} is LOADING; the command stopped after that on an error. The host job stopped on an"
                   " internal error (OperationalError).")
        refused = "Another history-archive command is running. Nothing was changed."
        loads = ((VIEW, "REQUESTED", "load_view"),)
        closes = ((CLOSED_VIEW, "CLOSING", "close_view"),)
        cases = (
            ("load interrupted", loads, LOAD_VIEW, load_view_out("interrupted", 2, error=("view_interrupted",
                                                                                           load_stopped)), 2,
             [f"archive: view {VIEW} stopped: {load_stopped}"]),
            ("close interrupted", closes, CLOSE_VIEW, close_view_out("interrupted", 2, error=("view_interrupted",
                                                                                              close_stopped)), 2,
             [f"archive: view {CLOSED_VIEW} stopped: {close_stopped}"]),
            ("load outcome unknown", loads, LOAD_VIEW, load_view_out("could_not_run", 2, error=("outcome_unknown",
                                                                                                 unknown)), 2,
             [f"history-archive load-view could not run: {unknown}",
              f"View {VIEW} stays LOADING: the next start fails it (recover) and removes the rows it loaded. Live"
              f" history was not changed. {VIEW_AGAIN}"]),
            ("close could not run", closes, CLOSE_VIEW, close_view_out("could_not_run", 2, error=(
                "database_unavailable", "PartFlow could not reach its database. Nothing was changed.")), 2,
             ["history-archive close-view could not run: PartFlow could not reach its database.",
              f"View {CLOSED_VIEW} stays CLOSING: the next start removes the rest of its rows."]),
            ("load refused", loads, LOAD_VIEW, load_view_out("refused", 1, error=("job_running", refused)), 1,
             [f"history-archive load-view refused: {refused}"]),
            ("no report", loads, LOAD_VIEW, out("", rc=137, stderr="Error response from daemon: container killed\n"), 2,
             ["history-archive load-view gave no valid report (exit 137;",
              f"the outcome of the load of view {VIEW} is unknown. A load never changes live history: the next start"
              " loads a view still REQUESTED, or fails a view left LOADING (recover), which removes the rows it"
              " loaded."]),
            ("a report of another view", loads, LOAD_VIEW, load_view_out(view_id=5), 2,
             ["history-archive load-view gave no valid report (exit 0;"]),
            ("a closing without a report", closes, CLOSE_VIEW, out("", rc=137), 2,
             [f"the outcome of the closing of view {CLOSED_VIEW} is unknown: the next start closes a view left CLOSING"
              " (or OPEN past its days) again."]),
        )
        for name, views, command, answer, code, texts in cases:
            with self.subTest(case=name):
                self.reset()
                self.within_the_hour()
                self.set_views(views=views)
                self.set_job(command, answer)
                self.archive("--scheduled")
                self.assertExit(code)
                self.assertEqual(self.order(), ["recover", "next", command, "status"])
                for text in texts:
                    self.assertIn(text, self.result.stderr)
                self.assertEqual(self.result.stdout, "")
                self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
                self.assertEqual(len(self.record_dirs()), 1)
                self.assertFalse(self.lock.exists())
                self.assertFalse(self.incident.exists())
        with self.subTest(case="no progress"):
            self.reset()
            self.within_the_hour()
            self.set_views(nexts=[next_view_out("load_view", "REQUESTED"), next_view_out("load_view", "REQUESTED")])
            self.archive("--scheduled")
            self.assertExit(2)
            self.assertEqual(self.order(), VIEW_ONLY)
            self.assertIn(f"history-archive next answered load_view again for view {VIEW}: a step of this start made no"
                          " progress", self.result.stderr)
        with self.subTest(case="interrupted by a signal"):
            for command, views, answer, text in (
                (LOAD_VIEW, loads, load_view_out(), f"interrupted during the load of view {VIEW}. A load never changes"
                 " live history: the next start loads a view still REQUESTED, or fails a view left LOADING (recover)"),
                (CLOSE_VIEW, closes, close_view_out(), f"interrupted during the closing of view {CLOSED_VIEW}: the next"
                 " start closes it again (a view left CLOSING, or OPEN past its days) and removes the rest of its"
                 " rows."),
            ):
                self.reset()
                self.at_the_slot()
                self.set_views(views=views, after=HAPPY)
                self.set_job(command, {**answer, "signal": "TERM"})
                self.archive("--scheduled")
                self.assertExit(2)
                self.assertIn(text, self.result.stderr)
                # The start worked on a view only: the hourly slot is left to the next start.
                self.assertEqual(self.stamp_value(), epoch_minute(11, 20))
                self.assertFalse(self.lock.exists())
                self.assertFalse(self.incident.exists())

    # AR-70 (contract B5 §3.5): recover fails a view that a stopped start left LOADING (its rows removed): exit 3, the
    # message says that live history was not changed and how to see it again. The cheap check takes a start for such a
    # view too; its recover does the work, and `next` then has no viewing work (the note says so).
    def test_ar70_recover_fails_a_stopped_load(self):
        self.within_the_hour()
        self.set_views(views=((VIEW, "LOADING", "recover"),))
        self.set_job("recover", recover_out(failed_view_ids=[VIEW]))
        self.archive("--scheduled")
        self.assertExit(3)
        self.assertEqual(self.order(), ["recover", "next", "status"])
        self.assertEqual(self.result.stdout, "")
        self.assertIn(f"view(s) {VIEW} stopped while LOADING and are FAILED now (an interrupted job, a load that stopped"
                      " on a database error, or a database restored from a backup taken during a load). The rows they"
                      f" had loaded were removed; live history was not changed. {VIEW_AGAIN}", self.result.stderr)
        self.assertIn("no view waits for the job any more (recover failed a load that a stopped start left, or a person"
                      " cancelled the view meanwhile), and the hourly work is not due: this start ends.",
                      self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="the work goes on"):
            self.reset()
            self.set_job("recover", recover_out(failed_view_ids=[VIEW]))
            self.archive()
            self.assertExit(3)
            self.assertEqual(self.order(), ["recover", "evaluate", *RUN_WORK])
        with self.subTest(case="a view that waits behind a run of another command"):
            self.reset()
            self.within_the_hour()
            self.set_views(nexts=[next_out("running", "RUNNING", view=view_object("REQUESTED"))])
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "next", "status"])
            self.assertEqual(self.result.stdout, "NOT_DUE\n")
            self.assertIn(f"view {VIEW} waits while run {RUN} is RUNNING in another history-archive command, and the"
                          " hourly work is not due: this start ends.", self.result.stderr)

    # AR-71 (lead decision 2): while the incident record exists, no viewing work either: a start at its slot (and a
    # manual one) runs recover and status only, exit 1, and the refusal names the views; a start within the hour ends
    # without Docker although a view waits.
    def test_ar71_no_viewing_work_while_the_incident_record_exists(self):
        self.write_incident(self.incident)
        self.at_the_slot()
        self.set_views()
        self.archive("--scheduled")
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
        self.assertEqual(self.result.stdout, "")
        self.restart()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertEqual((self.result.returncode, self.result.stdout), (0, "NOT_DUE\n"))
        self.assertEqual(self.calls("docker"), [])
        self.restart()
        self.archive()
        self.assertExit(1)
        self.assertEqual(self.order(), ["recover", "status"])
        self.assertTrue(self.incident.exists())

    # AR-72: a scheduled start that finds viewing work while another archive.sh holds the lock defers it (exit 0), and
    # says what waits: the viewing work, or the open deletion request and the viewing work.
    def test_ar72_lock_held(self):
        self.write_lock()
        self.within_the_hour()
        self.set_views()
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "DEFERRED archive_running\n")
        self.assertIn("is still working; the viewing work waits for a later start.", self.result.stderr)
        self.assertEqual(self.steps(), [])
        self.restart()
        self.set_rule(CHEAP, settings_out(pending=True, views=((VIEW, "REQUESTED", "load_view"),)))
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "DEFERRED archive_running\n")
        self.assertIn("is still working; the open deletion request and the viewing work wait for a later start.",
                      self.result.stderr)
        self.restart()
        self.at_the_slot()
        self.set_rule(CHEAP, settings_out(views=((VIEW, "REQUESTED", "load_view"),)))
        self.archive("--scheduled")
        self.assertIn("is still working; this hour's work is skipped, and the viewing work waits for a later start.",
                      self.result.stderr)

    # AR-73: the help and the header name the viewing work, its exits and the VIEW line.
    def test_ar73_help(self):
        result = self.archive("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("The viewing work\ncomes after the deletion and before the run work, at any start",
                     "VIEW <outcome> <view id> <state> for a start that worked on views and on no run (VIEW loaded 4 OPEN,",
                     "or the load of a view failed: it is FAILED", "no run, no deletion request and no view",
                     "carries out an open\n                          deletion request and the viewing work at once"):
            self.assertIn(text, result.stdout)
        header = ARCHIVE_SH.read_text(encoding="utf-8").split("\nset -eu\n", 1)[0]
        for text in ("`VIEW <outcome> <view id> <state>`", "`close_view`: close-view --view N",
                     "`load_view`: load-view --view N", "`load-view-<view id>` and `close-view-<view id>`"):
            self.assertIn(text, header)

    # AR-76 (review B5 m1): a view that changed after `next` handed it out (a person cancelled it before its load, or its
    # days were raised before its closing) is refused with wrong_state, which changed nothing: the start logs it and asks
    # `next` again instead of ending with exit 1. The final line names only a view that a step loaded or closed; the
    # DONE_STEPS rule still stops a `next` that hands the same view out again.
    def test_ar76_a_view_that_changed_meanwhile(self):
        cancelled = f"View {VIEW} is CANCELLED; only a REQUESTED view can be loaded. Nothing was changed."
        not_expired = (f"View {CLOSED_VIEW} is open until 2026-10-20T12:21:03.000000Z and nobody closed it. Nothing was"
                       " changed.")
        self.within_the_hour()
        self.set_views()
        self.set_job(LOAD_VIEW, load_view_out("refused", 1, error=("wrong_state", cancelled)))
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), VIEW_ONLY)
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertIn(f"archive: history-archive load-view refused view {VIEW}, which changed after next handed it out:"
                      f" {cancelled} This start asks next again.\n", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.assertFalse(self.lock.exists())
        with self.subTest(case="the days were raised before the closing, at the slot"):
            self.reset()
            self.at_the_slot()
            self.set_views(views=((CLOSED_VIEW, "OPEN", "close_view"), (VIEW, "REQUESTED", "load_view")))
            self.set_job(CLOSE_VIEW, close_view_out("refused", 1, error=("wrong_state", not_expired)))
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "evaluate", "next", CLOSE_VIEW, "next", LOAD_VIEW, "next",
                                            "status"])
            self.assertEqual(self.result.stdout, f"VIEW loaded {VIEW} OPEN\n")
            self.assertIn(f"history-archive close-view refused view {CLOSED_VIEW}, which changed after next handed it"
                          f" out: {not_expired} This start asks next again.", self.result.stderr)
            self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="no other work at the slot"):
            self.reset()
            self.at_the_slot()
            self.set_views(views=((CLOSED_VIEW, "OPEN", "close_view"),))
            self.set_job(CLOSE_VIEW, close_view_out("refused", 1, error=("wrong_state", not_expired)))
            self.archive("--scheduled")
            self.assertExit(0)
            self.assertEqual(self.order(), ["recover", "evaluate", "next", CLOSE_VIEW, "next", "status"])
            self.assertEqual(self.result.stdout, "EVALUATE off - -\n")
            self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        with self.subTest(case="the same view handed out again"):
            self.reset()
            self.within_the_hour()
            self.set_views(nexts=[next_view_out("load_view", "REQUESTED"), next_view_out("load_view", "REQUESTED")])
            self.set_job(LOAD_VIEW, load_view_out("refused", 1, error=("wrong_state", cancelled)))
            self.archive("--scheduled")
            self.assertExit(2)
            self.assertEqual(self.order(), VIEW_ONLY)
            self.assertIn(f"history-archive next answered load_view again for view {VIEW}: a step of this start made no"
                          " progress", self.result.stderr)


if __name__ == "__main__":
    unittest.main()
