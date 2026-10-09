"""P16-S6: deploy/production/scheduled-reconcile.sh against fake `docker`, `date`, `timeout` and `mv` executables (P16-S6
SPEC section 6.3, cases SR-1..SR-8).

The harness of test_check_script.py (itself the test_release_scripts.py fake-tool harness) is reused: the fake `timeout`
logs its arguments and runs the bounded command unless its rule answers 124; the fake `docker` answers the reconcile run
with a report on stdout; the real monitor_report.py applies the P16-S1 exit-code rule under a `python3` wrapper of this
interpreter. Nothing touches a Docker daemon, a database or the network.

Run with the other production tests:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_check_script import CheckHarness  # noqa: E402
from test_monitor_report import reconcile_doc  # noqa: E402
from test_release_scripts import SH_REQUIRED, find_sh, out, rule  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
RECONCILE_SH = REPO / "deploy" / "production" / "scheduled-reconcile.sh"
RECONCILE_RUN = r"(?:^| )run --rm --no-deps -T backend python -m app\.cli reconcile --max-findings (\d+)$"
ONE_OFF = (r"^ps -a -q --filter label=com\.docker\.compose\.project=partflow-production"
           r" --filter label=com\.docker\.compose\.service=backend --filter label=com\.docker\.compose\.oneoff=True$")
STAMP = "20261008T120000Z"


class ReconcileHarness(CheckHarness):
    def setUp(self):
        super().setUp()
        self.reports = self.tmp / "reports"
        self.records_dir = self.tmp / "deployments"
        self.rules["docker"] = [rule(r"config --quiet$"), rule(ONE_OFF, out(""))]
        self.rules["date"].append(rule(r"^-u \+%Y%m%dT%H%M%SZ$", out(STAMP + "\n")))
        self.set_report(reconcile_doc(0), 0)

    def set_report(self, text, rc):
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != RECONCILE_RUN] + [
            rule(RECONCILE_RUN, out(text, rc=rc, stderr="reconcile: clean\n"))]

    def reconcile(self, *arguments, defaults=True):
        base = ["--reports-dir", self.reports.as_posix(), "--records-dir", self.records_dir.as_posix()] if defaults else []
        return self.run_script(RECONCILE_SH, *base, *arguments)

    def report_path(self):
        return self.reports / f"{STAMP}-reconcile.json"

    def runs(self):
        return [c for c in self.docker() if re.search(RECONCILE_RUN, c)]


class ScheduledReconcile(ReconcileHarness):
    # SR-1
    def test_sr1_clean(self):
        self.reconcile()
        self.assertExit(0)
        report = self.report_path()
        self.assertEqual(self.result.stdout, f"RECONCILE clean {report.as_posix()} (0 findings, 2000 ms)\n")
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(re.search(RECONCILE_RUN, self.runs()[0]).group(1), "10000")
        bounded = [argv for argv in self.timeout_calls() if argv[1:3] == ["docker", "compose"] and "reconcile" in argv]
        self.assertEqual(len(bounded), 1)
        self.assertEqual(bounded[0][0], "3600")
        self.assertTrue(report.exists())
        self.assertTrue(Path(str(report) + ".log").exists())
        if os.name != "nt":
            self.assertEqual(self.reports.stat().st_mode & 0o777, 0o700)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(str(report) + ".log").stat().st_mode & 0o777, 0o600)
        last = (self.reports / "last-result.txt").read_text(encoding="utf-8").splitlines()
        self.assertRegex(last[0], r" exit 0$")
        self.assertEqual(last[1:], self.result.stdout.splitlines())
        text = RECONCILE_SH.read_text(encoding="utf-8")
        self.assertLess(text.index("\numask 077\n"), text.index("\nREPORT="))

    # SR-2
    def test_sr2_mismatch(self):
        self.set_report(reconcile_doc(1, failing=("a", "h")), 1)
        self.reconcile()
        self.assertExit(1)
        self.assertEqual(self.result.stdout.splitlines()[1:], ["FAIL a Title a: 1 findings", "FAIL h Title h: 1 findings"])

    # SR-3
    def test_sr3_could_not_run(self):
        for text, rc in (("", 1), (reconcile_doc(0), 1)):
            with self.subTest(rc=rc, empty=not text):
                self.reset()
                self.set_report(text, rc)
                self.reconcile()
                self.assertExit(2)
                self.assertEqual(self.result.stdout,
                                 f"RECONCILE could_not_run {self.report_path().as_posix()} (exit {rc}; no complete report)\n")

    # SR-4
    def test_sr4_release_running(self):
        lock = self.records_dir / ".release.lock"
        lock.mkdir(parents=True)
        self.reconcile()
        self.assertExit(2)
        self.assertEqual(self.result.stdout, f"RECONCILE could_not_run a release is running ({lock.as_posix()})\n")
        self.assertFalse(self.runs())
        last = (self.reports / "last-result.txt").read_text(encoding="utf-8").splitlines()
        self.assertRegex(last[0], r" exit 2$")
        self.assertEqual(last[1:], self.result.stdout.splitlines())
        lock.rmdir()
        self.reset_calls()
        self.lock.mkdir()
        (self.lock / "owner").write_text("host=h\npid=1\nstarted_at=2026-10-08T10:00:00Z\nby=release.sh\nrelease=/r/x\n",
                                         encoding="utf-8", newline="\n")
        self.reconcile()
        self.assertExit(2)
        self.assertIn("a release is running", self.result.stdout)
        self.assertFalse(self.runs())

    def reset_calls(self):
        calls = self.state / "calls.jsonl"
        if calls.exists():
            calls.unlink()

    # SR-5
    def test_sr5_arguments_and_env(self):
        cases = {
            "rehearsal without project": (self.env_lines, ("--rehearsal",)),
            "production project": (self.env_lines, ("--rehearsal", "--project", "partflow-production")),
            "bad project": (self.env_lines, ("--rehearsal", "--project", "Bad Name")),
            "project without rehearsal": (self.env_lines, ("--project", "pf-x")),
            "max findings": (self.env_lines, ("--max-findings", "0")),
            "max runtime": (self.env_lines, ("--max-runtime-minutes", "721")),
            "no backup dir": ([line for line in self.env_lines if not line.startswith("PARTFLOW_BACKUP_DIR=")], ()),
            "no release": ([line for line in self.env_lines if not line.startswith("PARTFLOW_RELEASE=")], ()),
        }
        for name, (lines, extra) in cases.items():
            with self.subTest(case=name):
                self.write_env(lines)
                self.reconcile(*extra)
                self.assertExit(2)
                self.assertTrue(self.result.stdout.startswith("RECONCILE could_not_run "), self.result.stdout)
                self.assertFalse(self.runs())
        self.write_env(self.env_lines)
        self.reconcile("--rehearsal", "--project", "pf-s6-rehearsal")
        self.assertExit(0)
        self.assertTrue(all(c.startswith("compose -p pf-s6-rehearsal ") for c in self.docker() if c.startswith("compose")))

    # SR-6
    def test_sr6_quiet(self):
        self.reconcile("--quiet")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "")
        self.assertEqual(self.result.stderr, "")
        self.assertIn("RECONCILE clean", (self.reports / "last-result.txt").read_text(encoding="utf-8"))
        self.set_report(reconcile_doc(1, failing=("a",)), 1)
        self.rules["date"][-1] = rule(r"^-u \+%Y%m%dT%H%M%SZ$", out("20261008T120100Z\n"))
        self.reconcile("--quiet")
        self.assertExit(1)
        self.assertEqual(len(self.result.stdout.splitlines()), 2)

    # SR-7
    def test_sr7_time_bound(self):
        self.prepend("timeout", rule(r"^60 docker compose .* reconcile ", out(rc=124)))
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != ONE_OFF]
        self.prepend("docker", rule(ONE_OFF, out("old-run\n"), out("old-run\nthis-run\n")), rule(r"^rm -f this-run$"))
        self.reconcile("--max-runtime-minutes", "1")
        self.assertExit(2)
        self.assertEqual(self.result.stdout,
                         f"RECONCILE could_not_run {self.report_path().as_posix()} (no answer within 1 min)\n")
        self.assertEqual([c for c in self.docker() if c.startswith("rm ")], ["rm -f this-run"])
        self.assertIn("exit 2", (self.reports / "last-result.txt").read_text(encoding="utf-8"))


class Static(unittest.TestCase):
    # SR-8
    def test_sr8_posix_sh_with_help(self):
        text = RECONCILE_SH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("\nset -eu\n", text)
        self.assertNotIn("\r", text)
        sh = find_sh()
        if sh is None:
            raise AssertionError(SH_REQUIRED)
        result = subprocess.run([sh, RECONCILE_SH.as_posix(), "--help"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: deploy/production/scheduled-reconcile.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
