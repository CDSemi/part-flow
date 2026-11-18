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
# The refusal of a start while the incident record of a reconcile after a purge exists (after the record's path).
INCIDENT_REFUSAL = (f" of a reconcile after a purge exists (run {RUN}, result mismatch): no run is worked on while it"
                    " exists, no export, verification, copy, pre-purge backup or purge, not even the resumed purge of a"
                    " run left PURGING. Follow OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge); once the"
                    " investigation is done and the reconciliation is clean, remove the record (rm ")


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


def job_doc(step, result, rc=0, run_id=None, state=None, error=None, **fields):
    """A `python -m app.cli history-archive <step>` report (contract §2.3 key order)."""
    document = {"report_version": 1, "command": f"history-archive {step.split()[0]}", "result": result,
                "exit_code": rc, "started_at": "2026-10-08T12:20:00Z", "finished_at": "2026-10-08T12:20:01Z",
                "duration_ms": 1000, "run_id": run_id, "state": state, **fields,
                "error": None if error is None else {"code": error[0], "message": error[1]}}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def job_out(step, result, rc=0, run_id=None, state=None, error=None, **fields):
    run = f" run {run_id}" if run_id is not None else ""
    summary = f"history-archive {step.split()[0]}:{run} {result}" + (f" ({state})" if state else "") + "\n"
    return out(job_doc(step, result, rc, run_id, state, error, **fields), rc=rc, stderr=summary)


def run_object(state, **fields):
    """The run summary of `next` and `status` (contract §3)."""
    document = {"id": RUN, "state": state, "cutoff": "2024-10-08T00:00:00Z", "requested_at": "2026-10-08T10:00:00Z",
                "archive_name": None if state == "APPROVED" else ARCHIVE_NAME, "manifest_sha256": None,
                "copied": False, "copy_confirmed": False, "skipped_components": None, "skipped_flows": None,
                "pre_purge_backup_name": None, "purged": None, "receipt_sha256": None, "finished_at": None,
                "result": None, "failure_reason": None}
    document.update(fields)
    return document


def next_out(action, state=None, rc=0, error=None):
    """A `history-archive next` answer: ACTION for run 3 in STATE (`none`: no open run); with `backup`, the pre-purge
    backup's kind and label; exit 3: the requester re-check failed the run."""
    if action == "none":
        return job_out("next", "none", run=None)
    fields = {"run": run_object(state)}
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


def status_ok():
    return job_out("status", "ok", open_run=None, last_finished_run=None,
                   archive_dir={"path": "/archive", "usable": True, "free_bytes": 1, "total_bytes": 2, "partial": []},
                   copy_dir=None, max_wait_days=7, receipts_not_copied=[], findings=[])


def backup_line(name=BACKUP, started=DUMP_STARTED):
    return out(f"BACKUP {name} /backups/{name} {started}\n", stderr="backup: verify ok (10 ms)\n")


HAPPY = (("export", "APPROVED"), ("verify", "RUNNING"), ("copy", "RUNNING"), ("backup", "RUNNING"))


def default_jobs(*nexts):
    """One approved run in one start (contract §3.1): export, verify, copy into the copy folder, the pre-purge backup,
    the purge and the reconcile after it. NEXTS: the successive answers of `next`, as (action, state)."""
    return [
        rule(MAINT + "settings$", job_out("settings", "read", start_minute=20, archive_dir="/archive", copy_dir=None,
                                          site_timezone="UTC")),
        rule(MAINT + "recover$", job_out("recover", "nothing_to_recover", failed_run_ids=[], removed_partial=[],
                                         kept_partial=[], copied_receipts=[], receipt_problems=[])),
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
        self.assertEqual(self.order(), ["recover", "next", f"export --run {RUN}", "next", f"verify --run {RUN}", "next",
                                        f"copy --run {RUN}", "next", "backup.sh", PURGE_WITH_BACKUP, "reconcile",
                                        "status"])
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
        for name in ("recover.json", "next.json", "next-2.json", "next-3.json", "next-4.json", "export.json",
                     "verify.json", "copy.json", "backup.out", "backup.log", "purge.json", "reconcile.json",
                     "reconcile.log", "status.json", "messages.log"):
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
        self.assertEqual(self.order(), ["recover", "next", f"export --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE nothing_eligible {RUN} NOTHING_ELIGIBLE\n")
        self.reset()
        self.set_next(("export", "APPROVED"), ("verify", "RUNNING"))
        self.set_job(f"verify --run {RUN}", job_out(
            "verify", "nothing_to_purge", run_id=RUN, state="COMPLETED_WITH_SKIPS", archive_name=ARCHIVE_NAME,
            manifest_sha256="a" * 64, skipped_components=1, skipped_flows=2,
            receipt={"name": f"{ARCHIVE_NAME}.purged.json", "sha256": "d" * 64, "copied_to": None, "copy_problem": None}))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.order(), ["recover", "next", f"export --run {RUN}", "next", f"verify --run {RUN}",
                                        "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE nothing_to_purge {RUN} COMPLETED_WITH_SKIPS\n")
        self.assertIn("nothing is left to purge", self.result.stderr)
        self.assertEqual(self.backup_calls(), [])

    # AR-3: nothing open, or a run that waits for its confirmation: no step for a run, no record kept.
    def test_ar3_nothing_to_do(self):
        for answer, stdout in ((("none",), "ARCHIVE none - -\n"),
                               (("wait", "WAITING_FOR_COPY"), f"ARCHIVE wait {RUN} WAITING_FOR_COPY\n")):
            with self.subTest(answer=answer):
                self.reset()
                self.set_next(answer)
                self.archive()
                self.assertExit(0)
                self.assertEqual(self.order(), ["recover", "next", "status"])
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
        self.assertEqual(self.order(), ["recover", "next", f"copy --run {RUN}", "next", "backup.sh", PURGE_WITH_BACKUP,
                                        "reconcile", "status"])
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
        self.assertEqual(self.order(), ["recover", "next", f"export --run {RUN}", "next", f"verify --run {RUN}",
                                        "status"])
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
        self.assertEqual(self.order(), ["recover", "next", f"export --run {RUN}", "status"])
        self.assertIn("history-archive export gave no valid report (exit 2;", self.result.stderr)
        self.assertIn("the outcome of export is unknown. The next start recovers a run left RUNNING", self.result.stderr)
        self.reset()
        self.set_job("next", out(job_doc("next", "none"), rc=1))
        self.archive()
        self.assertExit(2)
        self.assertEqual(self.order(), ["recover", "next", "status"])
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
        self.assertEqual(len(calls), 10)
        self.assertTrue(all(used for _, used in calls), calls)
        self.assertEqual(calls[8], (PURGE_WITH_BACKUP, True))
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
        self.prepend("df", rule(r"^-P .*archive-copy$", usb, usb, usb, usb, usb, usb, out(df_text(1, 2, "/"))))
        self.set_job(f"copy --run {RUN}", job_out("copy", "waiting_for_copy", run_id=RUN, state="WAITING_FOR_COPY",
                                                  archive_name=ARCHIVE_NAME, copied_to=None,
                                                  copy_problem="The copy folder /archive-copy does not exist or cannot be written."))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.copy_calls(), [("recover", True), ("next", True), (f"export --run {RUN}", True),
                                             ("next", True), (f"verify --run {RUN}", True), ("next", True),
                                             (f"copy --run {RUN}", False), ("status", False)])
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
        self.assertEqual(self.steps(), ["settings"])
        self.assertEqual(self.stamp_value(), epoch_minute(11, 20))
        self.assertFalse(self.lock.exists())
        self.assertIsNone(self.lock_during("settings"))
        # 12:15: within the hour of the 11:20 slot: no Docker at all.
        self.reset_calls()
        self.at(12, 15)
        self.archive("--scheduled")
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertEqual(self.calls("docker"), [])
        # 12:20: the first start at or after the Start minute does the hourly work.
        self.reset_calls()
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.order(), ["settings", "recover", "next", f"export --run {RUN}", "next",
                                        f"verify --run {RUN}", "next", f"copy --run {RUN}", "next", "backup.sh",
                                        PURGE_WITH_BACKUP, "reconcile", "status"])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        # 12:25 and 13:15: done for this hour.
        for hour, minute in ((12, 25), (13, 15)):
            self.reset_calls()
            self.at(hour, minute)
            self.archive("--scheduled")
            self.assertEqual(self.result.stdout, "NOT_DUE\n")
            self.assertEqual(self.calls("docker"), [])

    # AR-14: a late Start minute (58) is served by the first start of the next hour; a changed minute applies at once.
    def test_ar14_start_minute_from_the_settings(self):
        settings = MAINT + "settings$"
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != settings]
        self.prepend("docker", rule(settings, job_out("settings", "read", start_minute=58)))
        self.write_stamp(epoch_minute(11, 58))
        self.at(12, 55)
        self.archive("--scheduled")
        self.assertEqual((self.result.stdout, self.calls("docker")), ("NOT_DUE\n", []))
        self.at(13, 0)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertIn("recover", self.steps())
        self.assertEqual(self.stamp_value(), epoch_minute(12, 58))
        # The Start minute moved from 20 to 50 after the 12:20 slot: the 12:50 slot is due at the 13:20 start.
        self.reset()
        self.prepend("docker", rule(MAINT + "settings$", job_out("settings", "read", start_minute=50)))
        self.write_stamp(epoch_minute(12, 20))
        self.at(13, 20)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 50))
        self.assertIn("recover", self.steps())

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
        self.assertEqual((self.result.stdout, self.calls("docker")), ("NOT_DUE\n", []))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertIn("recover", self.steps())
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))

    # AR-16: a failure is reported once per hour: the stamp is written, the next start within the hour runs nothing.
    def test_ar16_failures_are_reported_once_per_hour(self):
        self.write_stamp(epoch_minute(11, 20))
        self.set_job("settings", out("", rc=2, stderr="could not reach the database\n"))
        self.at(12, 20)
        self.archive("--scheduled")
        self.assertExit(2)
        self.assertIn("the Start minute could not be read", self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.reset_calls()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertEqual((self.result.returncode, self.calls("docker")), (0, []))
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
        self.assertEqual(self.steps(), ["settings"])
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
        self.assertEqual(self.steps(), ["settings"])
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
        self.assertIn("take its pre-purge backup and PURGE its", help_text)
        self.assertNotIn("never deletes", help_text)


# ---------------------------------------------------------------------------
# AR-25..AR-28: check.sh archival_proposal
# ---------------------------------------------------------------------------


def archive_status(findings=(), rc=None, open_run=None, last=None, copy=False, receipts=()):
    rc = (1 if findings else 0) if rc is None else rc
    return job_doc("status", "finding" if findings else "ok", rc=rc, open_run=open_run, last_finished_run=last,
                   archive_dir={"path": "/archive", "usable": "archive_dir_unusable" not in findings,
                                "free_bytes": 5 * 1073741824, "total_bytes": 10 * 1073741824, "partial": []},
                   copy_dir=({"path": "/archive-copy", "usable": True, "free_bytes": 1073741824,
                              "total_bytes": 2 * 1073741824, "partial": []} if copy else None),
                   receipts_not_copied=list(receipts), max_wait_days=7, findings=list(findings))


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
                self.assertEqual(self.order(), ["recover", "next", "backup.sh", "status"])
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
        self.assertEqual(self.order(), ["recover", "next", f"purge --run {RUN}", "reconcile", "status"])
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
        self.assertEqual(self.order(), ["recover", "next", "status"])
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
                      f" {record.as_posix()}/reconcile.json. Stop requesting runs and follow OPERATIONS_RUNBOOK §8"
                      f" (Reconcile finding after a purge): the purge receipt is"
                      f" {sh_path(self.archive_dir)}/{ARCHIVE_NAME}.purged.json and the pre-purge backup {BACKUP}",
                      self.result.stderr.replace(sh_path(record), record.as_posix()))
        values = self.incident_values()
        self.assertEqual({k: values[k] for k in ("result", "exit_code", "run", "archive_name", "pre_purge_backup",
                                                 "failing_checks")},
                         {"result": "mismatch", "exit_code": "1", "run": "3", "archive_name": ARCHIVE_NAME,
                          "pre_purge_backup": BACKUP, "failing_checks": "c g"})
        self.assertRegex(values["finished_at"], r"^2026-10-08T12:20:30Z$")
        self.assertTrue(values["report"].endswith("/reconcile.json"), values["report"])
        self.assertIn("records the incident, and no start works on a run until you remove it after the investigation.",
                      self.result.stderr)
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
                self.assertEqual(self.order(), ["recover", "next", *steps, "next", "status"])
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
        self.assertEqual(self.order(), ["recover", "next", f"purge --run {RUN}", "reconcile", "status"])
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
        self.assertEqual(self.order(), ["settings", "recover", "status"])
        self.assertIn(INCIDENT_REFUSAL, self.result.stderr)
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        self.assertTrue(self.incident.exists())
        # The next 5-minute start of that hour ends at once, without Docker: the refusal is reported once per hour.
        self.reset_calls()
        self.at(12, 25)
        self.archive("--scheduled")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "NOT_DUE\n")
        self.assertEqual(self.calls("docker"), [])


if __name__ == "__main__":
    unittest.main()
