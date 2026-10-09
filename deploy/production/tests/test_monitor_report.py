"""P16-S6: deploy/production/monitor_report.py, the stdlib helper that turns a `status` or `reconcile` JSON report into
check lines (P16-S6 SPEC section 6.3, cases MR-1..MR-9). The helper runs as a subprocess of this interpreter on
temporary report files; nothing touches Docker or a database.

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
                   "latest_daily": None, "candidates": 0,
                   "lock": {"present": False, "by": None, "started_at": None, "name": None}},
        "archival": {"status": "not_applicable", "reason": ARCHIVAL_REASON, "awaiting_approval": None},
        "findings": [{"code": code, "message": message} for code, message in findings],
        "error": {"code": error[0], "message": error[1]} if error else None,
    }
    if backup != "not_applicable":
        document["backup"].update({
            "status": "finding" if any(c.startswith("backup_") for c, _ in findings) else "ok",
            "directory": "/backups", "candidates": 1,
            "latest": {"name": "20261008T020000Z-daily", "kind": "daily", "completed_at": "2026-10-08T02:00:30Z",
                       "age_hours": 3.4, "release_tag": "v1.0.0-rc.1", "alembic_revision": HEAD, "dump_bytes": 1024},
            "latest_daily": {"name": "20261008T020000Z-daily", "completed_at": "2026-10-08T02:00:30Z", "age_hours": 3.4},
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
            "PASS backup_age 20261008T020000Z-daily completed 3.4 h ago (limit 26 h)",
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
        self.assertEqual(lines[2], "PASS backup_age 20261008T020000Z-daily completed 3.4 h ago (limit 26 h)")
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
