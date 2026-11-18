"""P16-S6: deploy/production/monitor_report.py, the stdlib helper that turns a `status`, `history-archive status` or
`reconcile` JSON report into check lines (P16-S6 SPEC section 6.3, cases MR-1..MR-9; Movement-history archival adds
MR-10 (step 1), MR-11..MR-14 (step 2: the purge states and results, receipt_not_copied, the incident record of a
reconcile after a purge, a pre-purge backup as the newest backup) and MR-15..MR-17 (step 3: the open and the last
finished permanent deletion request, deletion_request_waiting and --deletion-wait-minutes, the incident record of a
reconcile after a deletion, the waiting request while archival is not set up)). The helper runs as a subprocess of this
interpreter
on temporary report files; nothing touches Docker or a database.

Run with the other production tests:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
"""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
MONITOR_REPORT = REPO / "deploy" / "production" / "monitor_report.py"
HEAD = "0032_phase14_route_adjusted"
ARCHIVAL_REASON = "Archival proposals arrive with P16-S10."


def status_doc(exit_code=0, findings=(), error=None, backup="ok", locks=None, database=True):
    """A `python -m app.cli status` report (P16-S6 SPEC section 4.4 key order)."""
    result = "error" if error else ("attention" if findings else "ok")
    reachable = database and not (error and error[0] == "database_unavailable")
    document = {
        "report_version": 1,
        "command": "status",
        "result": result,
        "exit_code": exit_code,
        "started_at": "2026-10-08T10:00:00Z", "finished_at": "2026-10-08T10:00:01Z", "duration_ms": 1000,
        "release": {"tag": "v1.0.0-rc.1", "commit": "0" * 40},
        "database": {
            "status": "ok" if reachable else "error", "name": "partflow" if reachable else None,
            "server_version": "16.14 (Debian 16.14-1.pgdg120+1)" if reachable else None,
            "connected_role": "partflow_app" if reachable else None,
            "size_bytes": 52428800 if reachable else None, "connections": 4 if reachable else None,
            "locks": (locks or {"waiting": 0, "longest_wait_seconds": 0.0}) if reachable else None,
        },
        "movements": {"status": "ok" if reachable else "not_run", "rows": 1234 if reachable else None,
                      "total_bytes": 1048576 if reachable else None},
        "schema": {"status": ("finding" if any(c == "schema_not_ready" for c, _ in findings) else "ok") if reachable else "not_run",
                   "readiness": "current" if reachable else None, "expected_revision": HEAD,
                   "database_revision": HEAD if reachable else None, "accepted_revision": None},
        "backup": {"status": "not_applicable", "directory": None, "max_age_hours": 26, "latest": None,
                   "latest_scheduled": None, "candidates": 0,
                   "lock": {"present": False, "by": None, "started_at": None, "name": None}},
        "archival": {"status": "not_applicable", "reason": ARCHIVAL_REASON, "awaiting_approval": None},
        "findings": [{"code": code, "message": message} for code, message in findings],
        "error": {"code": error[0], "message": error[1]} if error else None,
    }
    if backup != "not_applicable":
        document["backup"].update({
            "status": "finding" if any(c.startswith("backup_") for c, _ in findings) else "ok",
            "directory": "/backups", "candidates": 1,
            "latest": {"name": "20261008T020000Z-scheduled", "kind": "scheduled", "completed_at": "2026-10-08T02:00:30Z",
                       "age_hours": 3.4, "release_tag": "v1.0.0-rc.1", "alembic_revision": HEAD, "dump_bytes": 1024},
            "latest_scheduled": {"name": "20261008T020000Z-scheduled", "completed_at": "2026-10-08T02:00:30Z",
                                 "age_hours": 3.4},
        })
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def reconcile_doc(exit_code, failing=(), errors=(), badge=None):
    """A reconcile report; `failing` = check ids with one finding each, `errors` = check ids that ended in error."""
    checks = []
    for check_id in "abcdefghij":
        status, findings, reason = "pass", [], None
        if check_id in failing:
            status = "fail"
            findings = [{"code": f"{check_id.upper()}_FINDING", "entity": {"type": "worker", "id": 7}, "part_number": None,
                         "expected": "EXPECTED-1", "actual": badge or "ACTUAL-1", "detail": {"value": badge or "DETAIL-1"}}]
        elif check_id in errors:
            status, reason = "error", "The check exceeded the statement timeout of 300 s."
        checks.append({"id": check_id, "title": f"Title {check_id}", "status": status, "duration_ms": 1, "examined": {},
                       "finding_count": len(findings), "truncated": False, "reason": reason,
                       "error_code": "statement_timeout" if reason else None, "findings": findings})
    document = {
        "report_version": 1, "command": "reconcile", "result": {0: "clean", 1: "mismatch", 2: "error"}[exit_code],
        "exit_code": exit_code, "started_at": "2026-10-08T04:00:00Z", "finished_at": "2026-10-08T04:00:02Z",
        "duration_ms": 2000, "runtime": {}, "database": {}, "options": {}, "error": None, "checks": checks,
    }
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


class Helper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pf-s6-monitor-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def run_helper(self, *arguments):
        result = subprocess.run([sys.executable, "-B", str(MONITOR_REPORT), *map(str, arguments)], capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.result = result
        return result.returncode, result.stdout.splitlines()


class Status(Helper):
    # MR-1
    def test_mr1_ok(self):
        rc, lines = self.run_helper("status", self.write("s.json", status_doc()), 0)
        self.assertEqual(rc, 0, self.result.stderr)
        self.assertEqual(lines, [
            "PASS database partflow as partflow_app: 50.0 MiB, 4 connections, 0 lock waits (longest 0.0 s),"
            " 1234 Movements (1.0 MiB)",
            f"PASS schema current at {HEAD}",
            "PASS backup_age 20261008T020000Z-scheduled completed 3.4 h ago (limit 26 h)",
            f"SKIP archival_proposal {ARCHIVAL_REASON}",
        ])

    # MR-2
    def test_mr2_findings(self):
        stale = "The newest backup 20261007T020000Z-daily completed 27.1 hours ago; the limit is 26 hours."
        schema = (f"The database is at revision 9999_x but release v1.0.0-rc.1 expects {HEAD}: PartFlow refuses changes"
                  " until the release is completed or rolled back.")
        document = status_doc(1, findings=[("backup_stale", stale), ("schema_not_ready", schema)])
        rc, lines = self.run_helper("status", self.write("s.json", document), 1)
        self.assertEqual(rc, 1)
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(lines[0].startswith("PASS database "))
        self.assertEqual(lines[1], f"FAIL schema {schema}")
        self.assertEqual(lines[2], f"FAIL backup_age {stale}")
        self.assertEqual(lines[3], f"SKIP archival_proposal {ARCHIVAL_REASON}")

    # MR-3
    def test_mr3_database_unavailable(self):
        document = status_doc(2, error=("database_unavailable", "The PartFlow database could not be reached."))
        rc, lines = self.run_helper("status", self.write("s.json", document), 2)
        self.assertEqual(rc, 1)
        self.assertEqual(lines[0], "FAIL database The PartFlow database could not be reached.")
        self.assertEqual(lines[1], "SKIP schema not evaluated (database_unavailable)")
        self.assertEqual(lines[2], "PASS backup_age 20261008T020000Z-scheduled completed 3.4 h ago (limit 26 h)")
        self.assertEqual(lines[3], f"SKIP archival_proposal {ARCHIVAL_REASON}")

    # MR-4
    def test_mr4_invalid_reports(self):
        cases = {
            "empty": ("", 2),
            "invalid-json": ("{not json", 2),
            "exit-code-differs": (status_doc(0), 1),
            "other-command": (status_doc(0).replace('"command": "status"', '"command": "reconcile"'), 0),
            "other-version": (status_doc(0).replace('"report_version": 1', '"report_version": 2'), 0),
        }
        for name, (text, code) in cases.items():
            with self.subTest(case=name):
                rc, lines = self.run_helper("status", self.write(f"{name}.json", text), code)
                self.assertEqual(rc, 1)
                self.assertEqual(lines, [
                    f"FAIL database status could not run (exit {code}); see status.err",
                    "SKIP schema status did not run",
                    "SKIP backup_age status did not run",
                    "SKIP archival_proposal status did not run",
                ])
        rc, lines = self.run_helper("status", self.tmp / "missing.json", 125)
        self.assertEqual((rc, lines[0]), (1, "FAIL database status could not run (exit 125); see status.err"))

    # MR-5
    def test_mr5_backup_not_applicable(self):
        rc, lines = self.run_helper("status", self.write("s.json", status_doc(backup="not_applicable")), 0)
        self.assertEqual(rc, 1)
        self.assertEqual(lines[2], "FAIL backup_age status ran without the backup directory")

    # MR-6
    def test_mr6_growth_file(self):
        report = self.write("s.json", status_doc())
        growth = self.tmp / "growth.tsv"
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        expected_line = f"{today}\t52428800\t1234\t1048576\n"
        header = "date\tdatabase_bytes\tmovement_rows\tmovement_bytes\n"
        # Absent: created with the header and today's line.
        rc, lines = self.run_helper("status", report, 0, "--growth-file", growth)
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 4)
        self.assertEqual(growth.read_text(encoding="utf-8"), header + expected_line)
        # Last line dated today: unchanged.
        self.run_helper("status", report, 0, "--growth-file", growth)
        self.assertEqual(growth.read_text(encoding="utf-8"), header + expected_line)
        # Last line dated yesterday: appended.
        growth.write_text(header + f"{yesterday}\t1\t2\t3\n", encoding="utf-8", newline="\n")
        self.run_helper("status", report, 0, "--growth-file", growth)
        self.assertEqual(growth.read_text(encoding="utf-8"), header + f"{yesterday}\t1\t2\t3\n" + expected_line)
        # Unwritable (a directory stands in for the file): a NOTE line, the exit code unaffected.
        blocked = self.tmp / "blocked.tsv"
        blocked.mkdir()
        rc, lines = self.run_helper("status", report, 0, "--growth-file", blocked)
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 5, lines)
        self.assertTrue(lines[4].startswith("NOTE growth file not updated: "), lines[4])
        # A database that did not answer adds nothing.
        down = self.write("down.json", status_doc(2, error=("database_unavailable", "The PartFlow database could not be reached.")))
        other = self.tmp / "other.tsv"
        self.run_helper("status", down, 2, "--growth-file", other)
        self.assertFalse(other.exists())

    # MR-9
    def test_mr9_lock_waits(self):
        document = status_doc(locks={"waiting": 2, "longest_wait_seconds": 7.5})
        rc, lines = self.run_helper("status", self.write("s.json", document), 0)
        self.assertEqual(rc, 0)
        self.assertIn("2 lock waits (longest 7.5 s)", lines[0])

    def test_usage_errors(self):
        for arguments in ((), ("bogus",), ("status",), ("status", "x.json"), ("status", "x.json", "one"),
                          ("reconcile", "x.json"), ("status", "x.json", "0", "--growth-file")):
            with self.subTest(arguments=arguments):
                rc, lines = self.run_helper(*arguments)
                self.assertEqual(rc, 2)
                self.assertEqual(lines, [])

    def test_lines_are_single_and_bounded(self):
        long_message = "line one\nline two " + "x" * 600
        document = status_doc(1, findings=[("backup_stale", long_message)])
        rc, lines = self.run_helper("status", self.write("s.json", document), 1)
        self.assertEqual(rc, 1)
        self.assertEqual(len(lines), 4)
        self.assertLessEqual(len(lines[2]) - len("FAIL backup_age "), 300)
        self.assertIn("line one?line two", lines[2])


def archive_status_doc(exit_code=0, findings=(), open_run=None, last=None, error=None, receipts=(), deletion=None):
    """A `python -m app.cli history-archive status` report (Movement-history archival steps 1 and 2; DELETION, a pair of
    the open and the last finished deletion request, adds the keys of step 3)."""
    document = {
        "report_version": 1, "command": "history-archive status", "result": "finding" if findings else "ok",
        "exit_code": exit_code, "started_at": "2026-10-08T10:00:00Z", "finished_at": "2026-10-08T10:00:01Z",
        "duration_ms": 10, "run_id": None, "state": None, "open_run": open_run, "last_finished_run": last,
        "archive_dir": {"path": "/archive", "usable": True, "free_bytes": 3 * 1073741824,
                        "total_bytes": 4 * 1073741824, "partial": []},
        "copy_dir": None, "receipts_not_copied": list(receipts), "max_wait_days": 7, "findings": list(findings),
        "error": {"code": error[0], "message": error[1]} if error else None,
    }
    if deletion is not None:
        document["request_id"] = None
        document["open_deletion_request"], document["last_finished_deletion_request"] = deletion
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def deletion_summary(request_id, state, age_seconds=None, failure=None):
    """A deletion request summary of `history-archive status` (contract B3 §3)."""
    document = {"id": request_id, "state": state, "scope": {"work_order_numbers": ["WO-1"], "part_numbers": []},
                "requested_at": "2026-10-08T09:40:00Z", "requested_by_user_id": None,
                "requested_by_operator": "ops", "rows": {"work_orders": 1}, "started_at": None, "backup_label": None,
                "pre_purge_backup_name": None, "finished_at": None, "failure_reason": failure}
    if age_seconds is not None:
        document["age_seconds"] = age_seconds
    return document


def settings_doc(request=None, exit_code=0, error=None, started_at="2026-10-08T10:00:00.250000Z"):
    """The cheap check of archive.sh, a `history-archive settings` report (contract B3 §3; the backend writes times with
    microseconds), which check.sh reads while archival is not set up."""
    document = {
        "report_version": 1, "command": "history-archive settings", "result": "failed" if error else "read",
        "exit_code": exit_code, "started_at": started_at, "finished_at": "2026-10-08T10:00:00.350000Z",
        "duration_ms": 100, "run_id": None, "request_id": None, "state": None,
        "error": {"code": error[0], "message": error[1]} if error else None,
    }
    if not error:
        document.update({"start_minute": 20, "archive_dir": None, "copy_dir": None, "site_timezone": "UTC",
                         "deletion_pending": request is not None, "deletion_request": request})
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def run_summary(run_id, state, result=None, purged=None, skipped=(None, None), failure=None):
    """The run summary of `history-archive status` (contract §3)."""
    return {"id": run_id, "state": state, "cutoff": "2024-10-08T07:00:00Z", "requested_at": "2026-10-08T09:00:00Z",
            "archive_name": f"run-{run_id}-20261008T092000Z", "manifest_sha256": "a" * 64, "copied": True,
            "copy_confirmed": False, "skipped_components": skipped[0], "skipped_flows": skipped[1],
            "pre_purge_backup_name": None, "receipt_sha256": None, "finished_at": None, "result": result,
            "failure_reason": failure,
            "purged": None if purged is None else dict(zip(("components", "flows", "movements"), purged))}


class Archive(Helper):
    # MR-10 (archival step 1): the archival_proposal line from `history-archive status`.
    def test_mr10_archive_lines(self):
        rc, lines = self.run_helper("archive", self.write("a.json", archive_status_doc()), 0)
        self.assertEqual((rc, lines), (0, ["PASS archival_proposal no open run; no finished run; archive folder 3.0 GiB free"]))
        last = {"id": 2, "state": "FAILED", "failure_reason": "The copy into /archive-copy failed."}
        rc, lines = self.run_helper("archive", self.write("f.json", archive_status_doc(1, ["last_run_failed"], last=last)), 1)
        self.assertEqual((rc, lines), (1, ["FAIL archival_proposal last_run_failed: run 2 FAILED: The copy into"
                                           " /archive-copy failed."]))
        rc, lines = self.run_helper("archive", self.write("e.json", archive_status_doc(
            2, error=("database_unavailable", "PartFlow could not reach its database. Nothing was changed."))), 2)
        self.assertEqual((rc, lines), (1, ["FAIL archival_proposal PartFlow could not reach its database. Nothing was changed."]))
        for name, text, code in (("empty.json", "", 2), ("differs.json", archive_status_doc(0), 1),
                                 ("status.json", status_doc(), 0)):
            with self.subTest(case=name):
                rc, lines = self.run_helper("archive", self.write(name, text), code)
                self.assertEqual((rc, lines), (1, [f"FAIL archival_proposal history-archive status could not run (exit"
                                                   f" {code}); see archive-status.err"]))
        rc, lines = self.run_helper("archive", "x.json")
        self.assertEqual((rc, lines), (2, []))

    # MR-11 (archival step 2): the new states and results: a PURGING open run with what it purged so far; the last
    # finished run with its result, its purged counts and its skips (COMPLETED_WITH_SKIPS passes and names them).
    def test_mr11_states_and_results(self):
        cases = (
            (run_summary(4, "PURGING", purged=(40, 90, 1200)), None,
             "run 4 PURGING (40 components, 90 flows, 1200 Movements purged so far); no finished run"),
            (None, run_summary(3, "COMPLETED", "PURGED", (118, 305, 4190), (0, 0)),
             "no open run; last finished run 3 COMPLETED (PURGED: 118 components, 305 flows, 4190 Movements purged)"),
            (None, run_summary(3, "COMPLETED_WITH_SKIPS", "PURGED", (118, 305, 4190), (2, 5)),
             "no open run; last finished run 3 COMPLETED_WITH_SKIPS (PURGED: 118 components, 305 flows, 4190 Movements"
             " purged; 2 components, 5 flows skipped)"),
            (None, run_summary(3, "COMPLETED_WITH_SKIPS", "NOTHING_PURGED", (0, 0, 0), (3, 7)),
             "no open run; last finished run 3 COMPLETED_WITH_SKIPS (NOTHING_PURGED: 3 components, 7 flows skipped)"),
            (None, run_summary(3, "COMPLETED", "EXPORT_ONLY", None, (0, 0)),
             "no open run; last finished run 3 COMPLETED (EXPORT_ONLY)"),
            (None, run_summary(3, "NOTHING_ELIGIBLE"), "no open run; last finished run 3 NOTHING_ELIGIBLE"),
        )
        for open_run, last, text in cases:
            with self.subTest(text=text):
                rc, lines = self.run_helper("archive", self.write("a.json", archive_status_doc(open_run=open_run,
                                                                                             last=last)), 0)
                self.assertEqual((rc, lines), (0, [f"PASS archival_proposal {text}; archive folder 3.0 GiB free"]))

    # MR-12 (archival step 2): receipt_not_copied names the archives; a run that failed while purging says what it purged.
    def test_mr12_new_findings(self):
        document = archive_status_doc(1, ["receipt_not_copied"], receipts=["run-3-20261008T092000Z"])
        rc, lines = self.run_helper("archive", self.write("r.json", document), 1)
        self.assertEqual((rc, lines), (1, ["FAIL archival_proposal receipt_not_copied: the purge receipt of"
                                           " run-3-20261008T092000Z is missing or differs in the copy folder; the next"
                                           " archive.sh start copies it again"]))
        last = run_summary(3, "FAILED", purged=(12, 30, 400), failure="A purge batch deleted 9 Movements.")
        rc, lines = self.run_helper("archive", self.write("f.json", archive_status_doc(1, ["last_run_failed"], last=last)), 1)
        self.assertEqual((rc, lines), (1, ["FAIL archival_proposal last_run_failed: run 3 FAILED after purging 12"
                                           " components, 30 flows, 400 Movements: A purge batch deleted 9 Movements."]))

    # MR-13 (archival step 2): archive.sh's incident record of a reconcile after a purge fails the line first, whatever
    # the status report says; a record that is gone again adds nothing; an unreadable one fails.
    def test_mr13_incident_record(self):
        incident = self.write("incident", "result=mismatch\nexit_code=1\nrun=3\narchive_name=run-3-20261008T092000Z\n"
                                          "pre_purge_backup=x\nfailing_checks=c g\nfinished_at=2026-10-08T10:21:00Z\n"
                                          "report=/r/reconcile.json\n")
        expected = ("reconcile_after_purge: the reconcile after the purge of run 3 found a mismatch in checks c g at"
                    " 2026-10-08T10:21:00Z: an incident, OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge); report"
                    " /r/reconcile.json")
        clean = self.write("a.json", archive_status_doc())
        rc, lines = self.run_helper("archive", clean, 0, "--purge-reconcile", incident)
        self.assertEqual((rc, lines), (1, [f"FAIL archival_proposal {expected}"]))
        rc, lines = self.run_helper("archive", clean, 0, "--copy-reason", "x", "--purge-reconcile", incident)
        self.assertEqual((rc, lines[0][:60]), (1, f"FAIL archival_proposal {expected}"[:60]))
        rc, lines = self.run_helper("archive", self.write("e.json", ""), 2, "--purge-reconcile", incident)
        self.assertEqual((rc, lines), (1, [f"FAIL archival_proposal {expected}; history-archive status could not run (exit"
                                           " 2); see archive-status.err"]))
        for content, text in (
            ("result=could_not_run\nexit_code=2\nrun=4\n",
             "reconcile_after_purge: the reconcile after the purge of run 4 did not complete (could_not_run, exit 2): an"
             " incident, OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge)"),
            ("result=interrupted\nexit_code=2\nrun=4\nfailing_checks=\n",
             "reconcile_after_purge: the reconcile after the purge of run 4 did not end (archive.sh was interrupted): an"
             " incident, OPERATIONS_RUNBOOK §8 (Reconcile finding after a purge)"),
        ):
            with self.subTest(content=content):
                rc, lines = self.run_helper("archive", clean, 0, "--purge-reconcile", self.write("i2", content))
                self.assertEqual((rc, lines), (1, [f"FAIL archival_proposal {text}"]))
        rc, lines = self.run_helper("archive", clean, 0, "--purge-reconcile", self.tmp / "gone")
        self.assertEqual((rc, lines), (0, ["PASS archival_proposal no open run; no finished run; archive folder 3.0 GiB"
                                           " free"]))
        unreadable = self.tmp / "folder"
        unreadable.mkdir()
        rc, lines = self.run_helper("archive", clean, 0, "--purge-reconcile", unreadable)
        self.assertEqual(rc, 1)
        self.assertTrue(lines[0].startswith("FAIL archival_proposal reconcile_after_purge: the incident record"), lines)
        for arguments in (("archive", clean, 0, "--purge-reconcile"), ("archive", clean, 0, "--purge-reconcile", "a",
                                                                        "--purge-reconcile", "b")):
            with self.subTest(arguments=arguments):
                rc, lines = self.run_helper(*arguments)
                self.assertEqual((rc, lines), (2, []))

    # MR-15 (archival step 3): the open and the last finished deletion request in the terms of archive.sh's DELETION
    # line; an open request, APPROVED or RUNNING, requested longer ago than --deletion-wait-minutes (default 15) fails as
    # deletion_request_waiting, after the incident and before the job's findings; a FAILED last request never fails the
    # line.
    def test_mr15_deletion_requests(self):
        cases = (
            ((deletion_summary(7, "APPROVED", 120), None), (), 0,
             "PASS archival_proposal no open run; no finished run; archive folder 3.0 GiB free; deletion request 7"
             " APPROVED for 2 min"),
            ((deletion_summary(7, "RUNNING", 120), deletion_summary(6, "FAILED", failure="The data changed.")), (), 0,
             "PASS archival_proposal no open run; no finished run; archive folder 3.0 GiB free; deletion request 7"
             " RUNNING for 2 min; last deletion request 6 FAILED"),
            ((None, deletion_summary(6, "COMPLETED")), (), 0,
             "PASS archival_proposal no open run; no finished run; archive folder 3.0 GiB free; last deletion request 6"
             " COMPLETED"),
            ((deletion_summary(7, "APPROVED", 16 * 60), None), (), 1,
             "FAIL archival_proposal deletion_request_waiting: deletion request 7 APPROVED has waited 16 min (limit 15"
             " min), and the history archive task has not carried it out: is the task enabled? (SYNOLOGY_NAS §11.8)"),
            ((deletion_summary(7, "APPROVED", 16 * 60), None), ("--deletion-wait-minutes", "20"), 0,
             "PASS archival_proposal no open run; no finished run; archive folder 3.0 GiB free; deletion request 7"
             " APPROVED for 16 min"),
            ((deletion_summary(7, "APPROVED", 61), None), ("--deletion-wait-minutes", "1"), 1,
             "FAIL archival_proposal deletion_request_waiting: deletion request 7 APPROVED has waited 1 min (limit 1"
             " min), and the history archive task has not carried it out: is the task enabled? (SYNOLOGY_NAS §11.8)"),
        )
        for deletion, options, code, text in cases:
            with self.subTest(text=text):
                document = self.write("a.json", archive_status_doc(deletion=deletion))
                rc, lines = self.run_helper("archive", document, 0, *options)
                self.assertEqual((rc, lines), (code, [text]))
        # Review finding 3: a request stuck RUNNING (the job stopped and the task is disabled, so no start fails it)
        # fails the line; an APPROVED request behind a run that is RUNNING or PURGING names that run.
        cases = (
            (None, deletion_summary(7, "RUNNING", 3600),
             "deletion request 7 RUNNING has been open for 60 min (limit 15 min), and the history archive task has not"
             " finished it: is the task enabled? (SYNOLOGY_NAS §11.8)"),
            (run_summary(4, "RUNNING"), deletion_summary(7, "APPROVED", 16 * 60),
             "deletion request 7 APPROVED has waited 16 min (limit 15 min) behind run 4 RUNNING: the history archive"
             " task carries it out once that run's work in progress ends; is the task enabled? (SYNOLOGY_NAS §11.8)"),
            (run_summary(4, "PURGING", purged=(40, 90, 1200)), deletion_summary(7, "APPROVED", 50 * 60),
             "deletion request 7 APPROVED has waited 50 min (limit 15 min) behind run 4 PURGING: the history archive"
             " task carries it out once that run's work in progress ends; is the task enabled? (SYNOLOGY_NAS §11.8)"),
            (run_summary(4, "WAITING_FOR_COPY"), deletion_summary(7, "APPROVED", 16 * 60),
             "deletion request 7 APPROVED has waited 16 min (limit 15 min), and the history archive task has not"
             " carried it out: is the task enabled? (SYNOLOGY_NAS §11.8)"),
        )
        for open_run, request, text in cases:
            with self.subTest(text=text):
                document = self.write("r.json", archive_status_doc(open_run=open_run, deletion=(request, None)))
                rc, lines = self.run_helper("archive", document, 0)
                self.assertEqual((rc, lines), (1, [f"FAIL archival_proposal deletion_request_waiting: {text}"]))
        incident = self.write("incident", "result=mismatch\nexit_code=1\nrun=3\n")
        document = self.write("w.json", archive_status_doc(1, ["last_run_failed"], last={"id": 3, "state": "FAILED"},
                                                           deletion=(deletion_summary(7, "APPROVED", 3600), None)))
        rc, lines = self.run_helper("archive", document, 1, "--purge-reconcile", incident)
        self.assertEqual(rc, 1)
        # The incident first, the waiting request next, then the job's findings (the line is cut at 300 characters).
        self.assertTrue(lines[0].startswith("FAIL archival_proposal reconcile_after_purge: the reconcile after the purge"
                                            " of run 3 found a mismatch: an incident"), lines)
        self.assertIn("(Reconcile finding after a purge); deletion_request_waiting: deletion request 7 APPROVED has"
                      " waited 60 min (limit 15 min)", lines[0])
        rc, lines = self.run_helper("archive", document, 1)
        self.assertEqual(rc, 1)
        self.assertTrue(lines[0].startswith("FAIL archival_proposal deletion_request_waiting: deletion request 7"
                                            " APPROVED has waited 60 min"), lines)
        self.assertTrue(lines[0].endswith("; last_run_failed: run 3 FAILED: no reason recorded"), lines)
        for value in ("0", "1441", "x", "-5"):
            with self.subTest(value=value):
                rc, lines = self.run_helper("archive", document, 1, "--deletion-wait-minutes", value)
                self.assertEqual((rc, lines), (2, []))
                self.assertIn("--deletion-wait-minutes must be a whole number from 1 to 1440", self.result.stderr)

    # MR-16 (archival step 3): archive.sh's incident record after a deletion names the request, not a run.
    def test_mr16_incident_after_a_deletion(self):
        incident = self.write("incident", "result=mismatch\nexit_code=1\nrun=\narchive_name=\nrequest=7\n"
                                          "pre_purge_backup=x\nfailing_checks=c\nfinished_at=2026-10-08T10:21:00Z\n"
                                          "report=/r/reconcile.json\n")
        clean = self.write("a.json", archive_status_doc())
        rc, lines = self.run_helper("archive", clean, 0, "--purge-reconcile", incident)
        self.assertEqual((rc, lines), (1, ["FAIL archival_proposal reconcile_after_purge: the reconcile after the deletion"
                                           " of request 7 found a mismatch in checks c at 2026-10-08T10:21:00Z: an"
                                           " incident, OPERATIONS_RUNBOOK §8 (Reconcile finding after a deletion);"
                                           " report /r/reconcile.json"]))
        incident = self.write("i2", "result=interrupted\nexit_code=2\nrun=\nrequest=7\nfailing_checks=\n")
        rc, lines = self.run_helper("archive", clean, 0, "--purge-reconcile", incident)
        self.assertEqual((rc, lines), (1, ["FAIL archival_proposal reconcile_after_purge: the reconcile after the deletion"
                                           " of request 7 did not end (archive.sh was interrupted): an incident,"
                                           " OPERATIONS_RUNBOOK §8 (Reconcile finding after a deletion)"]))

    # MR-17 (archival step 3, review finding 2): while archival is not set up, `settings` reads the cheap check. An open
    # request, APPROVED or RUNNING, requested longer ago than --deletion-wait-minutes (the report's started_at minus the
    # request's requested_at) fails the line, since nothing carries it out; otherwise the line is SKIP with the reason
    # it is given, and with why when the report cannot tell.
    def test_mr17_settings_without_archival(self):
        skip = "archival is not set up: PARTFLOW_ARCHIVE_DIR is empty in .env.production (DEPLOYMENT §3.1)"
        approved = {**deletion_summary(7, "APPROVED"), "requested_at": "2026-10-08T09:40:00.000000Z"}
        running = {**deletion_summary(7, "RUNNING"), "requested_at": "2026-10-08T09:40:00.500000Z"}
        young = {**deletion_summary(7, "APPROVED"), "requested_at": "2026-10-08T09:57:00.000000Z"}
        cases = (
            (settings_doc(), 0, (), 0, f"SKIP archival_proposal {skip}"),
            (settings_doc(young), 0, (), 0, f"SKIP archival_proposal {skip}"),
            (settings_doc(approved), 0, ("--deletion-wait-minutes", "30"), 0, f"SKIP archival_proposal {skip}"),
            (settings_doc(approved), 0, (), 1,
             "FAIL archival_proposal deletion_request_waiting: deletion request 7 APPROVED has waited 20 min (limit 15"
             " min), but history archival is not set up (PARTFLOW_ARCHIVE_DIR is empty), so nothing carries it out: set"
             " up the history archive task (SYNOLOGY_NAS §11.8) or cancel the request in PartFlow"),
            (settings_doc(running, started_at="2026-10-08T10:00:00Z"), 0, (), 1,
             "FAIL archival_proposal deletion_request_waiting: deletion request 7 RUNNING has been open for 19 min"
             " (limit 15 min), but history archival is not set up (PARTFLOW_ARCHIVE_DIR is empty), so nothing finishes"
             " it: set up the history archive task (SYNOLOGY_NAS §11.8), whose next start makes it FAILED"),
            ("", 1, (), 0,
             f"SKIP archival_proposal {skip}; the deletion request check (history-archive settings in the backend"
             " container) could not run (exit 1); see archive-settings.err"),
            (settings_doc(exit_code=0), 2, (), 0,
             f"SKIP archival_proposal {skip}; the deletion request check (history-archive settings in the backend"
             " container) could not run (exit 2); see archive-settings.err"),
            (settings_doc(exit_code=2, error=("database_unavailable", "PartFlow could not reach its database. Nothing"
                                                                      " was changed.")), 2, (), 0,
             f"SKIP archival_proposal {skip}; the deletion request check failed: PartFlow could not reach its database."
             " Nothing was changed."),
            (settings_doc({**approved, "requested_at": "yesterday"}), 0, (), 0,
             f"SKIP archival_proposal {skip}; the deletion request check could not tell how long deletion request 7"
             " APPROVED has been open"),
        )
        for text, rc, options, code, expected in cases:
            with self.subTest(expected=expected):
                rc_out, lines = self.run_helper("settings", self.write("s.json", text), rc, "--skip-reason", skip,
                                                *options)
                self.assertEqual((rc_out, lines), (code, [expected]))
        document = self.write("s.json", settings_doc())
        for arguments in (("settings", document, 0), ("settings", document, 0, "--skip-reason", ""),
                          ("settings", document, 0, "--skip-reason", "x", "--deletion-wait-minutes", "0")):
            with self.subTest(arguments=arguments):
                rc, lines = self.run_helper(*arguments)
                self.assertEqual((rc, lines), (2, []))

    # MR-14 (archival step 2): backup_age accepts a pre-purge backup as the newest backup.
    def test_mr14_pre_purge_backup_is_the_newest(self):
        name = "20261008T092030Z-pre-purge-run-3-20261008T092000Z"
        document = json.loads(status_doc())
        document["backup"]["latest"].update({"name": name, "kind": "pre-purge", "age_hours": 0.6})
        rc, lines = self.run_helper("status", self.write("s.json", json.dumps(document, indent=2) + "\n"), 0)
        self.assertEqual(rc, 0)
        self.assertEqual(lines[2], f"PASS backup_age {name} completed 0.6 h ago (limit 26 h)")


class Reconcile(Helper):
    # MR-7
    def test_mr7_results(self):
        path = self.write("clean.json", reconcile_doc(0))
        rc, lines = self.run_helper("reconcile", path, 0)
        self.assertEqual((rc, lines), (0, [f"RECONCILE clean {path} (0 findings, 2000 ms)"]))

        path = self.write("mismatch.json", reconcile_doc(1, failing=("a", "h")))
        rc, lines = self.run_helper("reconcile", path, 1)
        self.assertEqual(rc, 1)
        self.assertEqual(lines, [f"RECONCILE mismatch {path} (2 findings, 2000 ms)",
                                 "FAIL a Title a: 1 findings", "FAIL h Title h: 1 findings"])

        path = self.write("error.json", reconcile_doc(2, errors=("c",)))
        rc, lines = self.run_helper("reconcile", path, 2)
        self.assertEqual(rc, 2)
        self.assertEqual(lines, [f"RECONCILE error {path} (0 findings, 2000 ms)",
                                 "FAIL c Title c: The check exceeded the statement timeout of 300 s."])

        for name, text, code in (("invalid.json", "", 1), ("garbage.json", "{", 0),
                                 ("differs.json", reconcile_doc(0), 1), ("status.json", status_doc(), 0)):
            with self.subTest(case=name):
                path = self.write(name, text)
                rc, lines = self.run_helper("reconcile", path, code)
                self.assertEqual(rc, 2)
                self.assertEqual(lines, [f"RECONCILE could_not_run {path} (exit {code}; no complete report)"])

    # MR-8
    def test_mr8_badge_values_never_printed(self):
        path = self.write("j.json", reconcile_doc(1, failing=("j",), badge="BADGE-SECRET-1"))
        self.assertIn("BADGE-SECRET-1", path.read_text(encoding="utf-8"))
        rc, lines = self.run_helper("reconcile", path, 1)
        self.assertEqual(rc, 1)
        self.assertEqual(lines[1], "FAIL j Title j: 1 findings")
        output = self.result.stdout + self.result.stderr
        self.assertNotIn("BADGE-SECRET-1", output)
        for value in ("EXPECTED-1",):
            self.assertNotIn(value, output)


if __name__ == "__main__":
    unittest.main()
