"""Movement-history archival, build step 1 (exports only, never deletes): deploy/production/archive.sh against the fake
executables of test_backup_scripts.py (docker, df, ps, uname, id, curl, sleep) plus a fake `date` that supplies the
clock; the archive lock seen from release.sh (the release.sh harness of test_release_scripts.py); `pf archive` (the pf
harness of test_pf_script.py, beside a stub archive.sh); and the archival_proposal line of check.sh (the check.sh
harness of test_check_script.py, with the real monitor_report.py). Cases:
  AR-1..AR-4   the hourly work: the step order of contract §2.4 and the ARCHIVE line
  AR-5..AR-9   exit codes 1, 2 and 3 and the records that are kept
  AR-10..AR-12 the optional copy folder: mounted with -v only when usable and on another file system
  AR-13..AR-17 --scheduled: the hourly stamp, the Start minute from `history-archive settings`, once per hour
  AR-18..AR-23 the locks in both directions (archive.sh refuses while a release runs; release.sh refuses while the
               archive lock is held; a stale archive lock; WAITING_FOR_COPY never holds the lock)
  AR-24        pf archive
  AR-25..AR-28 check.sh archival_proposal from `history-archive status`
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
from test_pf_script import PfHarness  # noqa: E402
from test_release_scripts import (  # noqa: E402
    RELEASE_SH, SH_REQUIRED, Harness as ReleaseHarness, find_sh, out, rule, sh_path,
)

REPO = Path(__file__).resolve().parents[3]
ARCHIVE_SH = REPO / "deploy" / "production" / "archive.sh"
# Git Bash may rewrite the -v value for a native fake (as a path list): match it loosely.
MAINT = r"--profile ops run --rm --no-deps -T --user 1000:1000 (-v .+ )?maintenance "
MAINTENANCE_PS = (r"^ps -q --filter label=com\.docker\.compose\.project=partflow-production"
                  r" --filter label=com\.docker\.compose\.service=maintenance$")
# 2026-10-08T12:00:00Z, a whole hour (the fake clock is UTC, so the local minute is the UTC minute).
BASE = calendar.timegm((2026, 10, 8, 12, 0, 0))
RUN = 3
BOOT = "boot-a"


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


def run_object(state):
    return {"id": RUN, "state": state, "cutoff": "2024-10-08T00:00:00Z", "requested_at": "2026-10-08T10:00:00Z",
            "archive_name": None if state == "APPROVED" else "run-3-20261008T122000Z", "manifest_sha256": None,
            "copy_confirmed": False, "finished_at": None, "result": None, "failure_reason": None}


def status_ok():
    return job_out("status", "ok", open_run=None, last_finished_run=None,
                   archive_dir={"path": "/archive", "usable": True, "free_bytes": 1, "total_bytes": 2, "partial": []},
                   copy_dir=None, max_wait_days=7, findings=[])


def default_jobs(next_result="export", state="APPROVED"):
    """The happy path of one approved run: export, verify, copy into the copy folder, completed."""
    return [
        rule(MAINT + "settings$", job_out("settings", "read", start_minute=20, archive_dir="/archive", copy_dir=None,
                                          site_timezone="UTC")),
        rule(MAINT + "recover$", job_out("recover", "nothing_to_recover", failed_run_ids=[], removed_partial=[],
                                         kept_partial=[])),
        rule(MAINT + "next$", job_out("next", next_result, run_id=RUN if next_result != "none" else None,
                                      state=state if next_result != "none" else None,
                                      run=run_object(state) if next_result != "none" else None)),
        rule(MAINT + f"export --run {RUN}$", job_out("export", "exported", run_id=RUN, state="RUNNING",
                                                     archive_name="run-3-20261008T122000Z", manifest_sha256="a" * 64,
                                                     archive_bytes=10, counts={"components": 1, "flows": 2,
                                                                               "movements": 3, "lineage_edges": 0,
                                                                               "assigned_routes": 0})),
        rule(MAINT + f"verify --run {RUN}$", job_out("verify", "verified", run_id=RUN, state="RUNNING",
                                                     archive_name="run-3-20261008T122000Z", manifest_sha256="a" * 64,
                                                     skipped_components=0, skipped_flows=0)),
        rule(MAINT + f"copy --run {RUN}$", job_out("copy", "completed", run_id=RUN, state="COMPLETED",
                                                   archive_name="run-3-20261008T122000Z",
                                                   copied_to="/archive-copy/run-3-20261008T122000Z", copy_problem=None)),
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
        self.rules["docker"] = default_jobs()
        self.set_clock(BASE + 20 * 60 + 30)
        self.watch([self.lock / "owner"])
        # archive.sh runs from a copy whose PROC names a fake /proc (this boot BOOT; pid 4242 started at tick 777).
        self.proc = fake_proc(self.tmp / "proc", [(4242, 777)])
        self.archive_sh = with_proc(ARCHIVE_SH, self.root / "deploy" / "production" / "archive.sh", self.proc)

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

    def maintenance_calls(self):
        return [label for label in self.docker() if re.search(r" maintenance ", label)]

    def set_job(self, step, answer):
        pattern = MAINT + re.escape(step) + "$"
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != pattern]
        self.rules["docker"].insert(0, rule(pattern, answer))

    def write_stamp(self, value):
        self.stamp.parent.mkdir(parents=True, exist_ok=True)
        self.stamp.write_text(f"{value}\n", encoding="utf-8", newline="\n")

    def stamp_value(self):
        return int(self.stamp.read_text(encoding="utf-8").strip()) if self.stamp.exists() else None

    def record_dirs(self):
        root = self.records / "archive"
        return sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []

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


# ---------------------------------------------------------------------------
# AR-1..AR-4: the hourly work
# ---------------------------------------------------------------------------


class HourlyWork(ArchiveHarness):
    # AR-1: an approved run is exported, verified and copied in one start; the lock is held throughout.
    def test_ar1_export_verify_copy(self):
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.steps(), ["recover", "next", f"export --run {RUN}", f"verify --run {RUN}",
                                        f"copy --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        for label in self.maintenance_calls():
            self.assertTrue(label.startswith("--profile ops run --rm --no-deps -T --user 1000:1000 maintenance "), label)
        owner = self.lock_during("recover")
        self.assertIsNotNone(owner)
        self.assertIn("by=archive.sh\n", owner)
        self.assertIn(f"boot={BOOT}\n", owner)
        self.assertRegex(owner, r"(?m)^start=[0-9]*$")
        self.assertIn("host=bs-host\n", owner)
        self.assertFalse(self.lock.exists())
        records = self.record_dirs()
        self.assertEqual(len(records), 1)
        for name in ("recover", "next", "export", "verify", "copy", "status"):
            self.assertTrue((records[0] / f"{name}.json").is_file(), name)
        self.assertIn("history-archive copy: run 3 completed (COMPLETED)", self.result.stderr)
        # A manual start never writes the hourly stamp and never reads the settings.
        self.assertIsNone(self.stamp_value())
        self.assertNotIn("settings", self.steps())

    # AR-2: nothing eligible ends the run after the export (no verify, no copy).
    def test_ar2_nothing_eligible(self):
        self.set_job(f"export --run {RUN}", job_out("export", "nothing_eligible", run_id=RUN, state="NOTHING_ELIGIBLE",
                                                    counts={"components": 0}))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.steps(), ["recover", "next", f"export --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE nothing_eligible {RUN} NOTHING_ELIGIBLE\n")

    # AR-3: nothing open, or a run that waits for its confirmation: no step for a run, no record kept.
    def test_ar3_nothing_to_do(self):
        for result, state, stdout in (("none", None, "ARCHIVE none - -\n"),
                                      ("wait", "WAITING_FOR_COPY", f"ARCHIVE wait {RUN} WAITING_FOR_COPY\n")):
            with self.subTest(result=result):
                self.reset()
                self.rules["docker"] = default_jobs(result, state or "APPROVED")
                self.archive()
                self.assertExit(0)
                self.assertEqual(self.steps(), ["recover", "next", "status"])
                self.assertEqual(self.result.stdout, stdout)
                self.assertEqual(self.record_dirs(), [])
                self.assertFalse(self.lock.exists())

    # AR-4: a confirmed (or copyable) waiting run is finished by copy alone; a copy that still waits keeps no record.
    def test_ar4_copy_of_a_waiting_run(self):
        self.rules["docker"] = default_jobs("copy", "WAITING_FOR_COPY")
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.steps(), ["recover", "next", f"copy --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, f"ARCHIVE completed {RUN} COMPLETED\n")
        self.assertEqual(len(self.record_dirs()), 1)
        self.reset()
        self.rules["docker"] = default_jobs("copy", "WAITING_FOR_COPY")
        self.set_job(f"copy --run {RUN}", job_out("copy", "waiting_for_copy", run_id=RUN, state="WAITING_FOR_COPY",
                                                  archive_name="run-3-x", copied_to=None,
                                                  copy_problem="The copy folder /archive-copy does not exist or cannot be written."))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.result.stdout, f"ARCHIVE waiting_for_copy {RUN} WAITING_FOR_COPY\n")
        self.assertIn("The copy folder /archive-copy does not exist or cannot be written.", self.result.stderr)
        self.assertIn("confirm it in Administration", self.result.stderr)
        self.assertEqual(self.record_dirs(), [])


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
        self.assertEqual(self.steps(), ["recover", "next", f"export --run {RUN}", f"verify --run {RUN}", "status"])
        self.assertEqual(self.result.stdout, "")
        self.assertIn(f"archive: run {RUN} FAILED: {reason} Nothing was deleted. Request a new run in Administration"
                      " → History archival & purge (OPERATIONS_RUNBOOK §11).", self.result.stderr)
        self.assertEqual(len(self.record_dirs()), 1)
        self.assertFalse(self.lock.exists())

    # AR-6: a refusal (another history-archive command holds the job's database lock) changes nothing: exit 1.
    def test_ar6_refused_step(self):
        self.set_job("recover", job_out("recover", "refused", rc=1,
                                        error=("job_running", "Another history-archive command is running. Nothing was changed.")))
        self.archive()
        self.assertExit(1)
        self.assertEqual(self.steps(), ["recover", "status"])
        self.assertIn("history-archive recover refused: Another history-archive command is running.", self.result.stderr)

    # AR-7: a report without its exit-code line (or none at all) is "could not run", the outcome unknown: exit 2.
    def test_ar7_invalid_report(self):
        self.set_job(f"export --run {RUN}", out("", rc=2, stderr="Error response from daemon: oops\n"))
        self.archive()
        self.assertExit(2)
        self.assertEqual(self.steps(), ["recover", "next", f"export --run {RUN}", "status"])
        self.assertIn("history-archive export gave no valid report (exit 2;", self.result.stderr)
        self.assertIn("the outcome of export is unknown. The next start recovers a run left RUNNING", self.result.stderr)
        self.reset()
        self.set_job("next", out(job_doc("next", "none"), rc=1))
        self.archive()
        self.assertExit(2)
        self.assertEqual(self.steps(), ["recover", "next", "status"])

    # AR-8: recover found a run left RUNNING by an earlier start: it is FAILED (exit 3); the work still continues.
    def test_ar8_recovered_stale_run(self):
        self.set_job("recover", job_out("recover", "recovered", failed_run_ids=[2],
                                        removed_partial=["/archive/.partial/run-2-20261008T112000Z"],
                                        kept_partial=["/archive/.partial/unknown"]))
        self.archive()
        self.assertExit(3)
        self.assertEqual(self.steps(), ["recover", "next", f"export --run {RUN}", f"verify --run {RUN}",
                                        f"copy --run {RUN}", "status"])
        self.assertIn("run(s) 2 stopped while RUNNING and are FAILED now.", self.result.stderr)
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
                                                                           "last_run_failed"]))
        self.archive()
        self.assertExit(0)
        self.assertIn("status findings: copy_confirmation_overdue last_run_failed", self.result.stderr)

    def test_usage(self):
        result = self.archive("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: deploy/production/archive.sh", result.stdout)
        for arguments, message in ((("--bogus",), "unknown argument --bogus."),
                                   (("--scheduled", "--scheduled"), "--scheduled is given twice."),
                                   (("--env-file",), "--env-file needs a value.")):
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
        # Step 1 never deletes: no purge step is ever run.
        self.assertNotRegex(text, r"maintenance purge|purge --run")


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

    # AR-10: a usable copy folder on another device is mounted at /archive-copy for every step, by a long-syntax bind
    # that never creates a missing source (`docker compose run -v` would).
    def test_ar10_mounted(self):
        self.configure_copy()
        step = MAINT + "recover$"
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != step]
        self.prepend("docker", rule(step, {**job_out("recover", "nothing_to_recover", failed_run_ids=[],
                                                     removed_partial=[], kept_partial=[]), "run": KEEP_OVERRIDE}))
        self.archive()
        self.assertExit(0)
        calls = self.copy_calls()
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(used for _, used in calls), calls)
        override = (self.state / "override.yaml").read_text(encoding="utf-8")
        self.assertIn("services:\n    maintenance:\n        volumes:\n            - type: bind\n", override)
        self.assertRegex(override, r"source: '\S*archive-copy'\n")
        self.assertIn("target: /archive-copy\n", override)
        self.assertIn("create_host_path: false\n", override)

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

    # AR-12b: the rule is evaluated again before every step: a disk that drops out during the export (its path now on
    # `/`) is not used by the copy.
    def test_ar12b_dropped_out_before_the_copy(self):
        self.configure_copy()
        usb = out(df_text(1, 2, "/mnt/usb"))
        self.prepend("df", rule(r"^-P .*archive-copy$", usb, usb, usb, usb, out(df_text(1, 2, "/"))))
        self.archive()
        self.assertExit(0)
        self.assertEqual(self.copy_calls(), [("recover", True), ("next", True), (f"export --run {RUN}", True),
                                             (f"verify --run {RUN}", True), (f"copy --run {RUN}", False),
                                             ("status", False)])
        self.assertIn("is on the host's root file system (/)", self.result.stderr)


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
        self.assertEqual(self.steps(), ["settings", "recover", "next", f"export --run {RUN}", f"verify --run {RUN}",
                                        f"copy --run {RUN}", "status"])
        self.assertEqual(self.stamp_value(), epoch_minute(12, 20))
        # 12:25 and 13:15: done for this hour.
        for hour, minute in ((12, 25), (13, 15)):
            self.reset_calls()
            self.at(hour, minute)
            self.archive("--scheduled")
            self.assertEqual(self.result.stdout, "NOT_DUE\n")
            self.assertEqual(self.calls("docker"), [])

    def reset_calls(self):
        calls = self.state / "calls.jsonl"
        if calls.exists():
            calls.unlink()

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

    # AR-19: a busy archive lock whose job container still runs: the scheduled start defers (exit 0), a manual one
    # refuses; the lock is never touched.
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
        self.archive()
        self.assertExit(1)
        self.assertIn("refused — the history archive job is running (archive.sh on bs-host, pid 4242", self.result.stderr)
        self.assertTrue((self.lock / "owner").is_file())

    # AR-19b: between two steps (owner pid alive, no job container) a scheduled start defers too (exit 0).
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
        self.rules["docker"] = default_jobs("wait", "WAITING_FOR_COPY")
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

    # AR-23: release.sh refuses while archive.sh holds the archive lock (before any record or Docker command), and
    # continues past a lock its owner provably left behind.
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
        # A job container still runs although the pid is gone: still refused.
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
        self.rules["archive.sh"] = [rule(r".*", out("", rc=3))]
        self.pf("archive", "--env-file", ".env.production")
        self.assertExit(3)
        self.pf("archive", "extra")
        self.assertExit(2)
        self.assertIn("pf archive takes no argument extra.", self.result.stderr)


# ---------------------------------------------------------------------------
# AR-25..AR-28: check.sh archival_proposal
# ---------------------------------------------------------------------------


def archive_status(findings=(), rc=None, open_run=None, last=None, copy=False):
    rc = (1 if findings else 0) if rc is None else rc
    return job_doc("status", "finding" if findings else "ok", rc=rc, open_run=open_run, last_finished_run=last,
                   archive_dir={"path": "/archive", "usable": "archive_dir_unusable" not in findings,
                                "free_bytes": 5 * 1073741824, "total_bytes": 10 * 1073741824, "partial": []},
                   copy_dir=({"path": "/archive-copy", "usable": True, "free_bytes": 1073741824,
                              "total_bytes": 2 * 1073741824, "partial": []} if copy else None),
                   max_wait_days=7, findings=list(findings))


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

    # AR-25: PASS from `history-archive status` (the open run, the last finished run, the free space).
    def test_ar25_pass(self):
        last = {**run_object("COMPLETED"), "result": "EXPORT_ONLY"}
        waiting = {**run_object("WAITING_FOR_COPY"), "id": 4, "age_seconds": 90000, "waiting_seconds": 2 * 86400}
        text = self.archival(archive_status(open_run=waiting, last=last), 0)
        self.assertEqual(text, "PASS archival_proposal run 4 WAITING_FOR_COPY waiting 2.0 days for its copy"
                               " confirmation (limit 7 days); last finished run 3 COMPLETED; archive folder 5.0 GiB free")
        self.assertExit(0)
        runs = self.archive_runs()
        self.assertEqual(len(runs), 1)
        self.assertNotIn(" -v ", runs[0])
        self.assertTrue((self.monitoring / "archive-status.json").is_file())
        # The `status` report's own archival line is replaced, never printed twice.
        self.assertEqual(len([t for t in self.lines() if " archival_proposal " in t]), 1)

    # AR-26: FAIL naming every finding.
    def test_ar26_findings(self):
        last = {**run_object("FAILED"), "failure_reason": "The host job stopped while the run was RUNNING."}
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


if __name__ == "__main__":
    unittest.main()
