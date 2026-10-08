"""P16-S3: deploy/production/reconcile_regression.py (P16-S3 SPEC section 6.3, cases RC-1..RC-7).

The script runs as a subprocess of this interpreter on report files written to a temporary directory, exactly as
release.sh calls it (`python3 reconcile_regression.py PRE POST`).
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "reconcile_regression.py"


def report(exit_code=1, checks=None):
    """A reconcile report; `checks` = {check id: (status, [(code, type, id)], truncated)}."""
    document = {"report_version": 1, "command": "reconcile", "exit_code": exit_code, "error": None, "checks": []}
    for check_id, (status, findings, truncated) in (checks or {}).items():
        document["checks"].append({
            "id": check_id, "status": status, "finding_count": len(findings), "truncated": truncated,
            "findings": [{"code": code, "entity": {"type": kind, "id": ident}} for code, kind, ident in findings],
        })
    return document


class ReconcileRegression(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pf-s3-regression-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def compare(self, pre, post):
        paths = []
        for name, document in (("pre.json", pre), ("post.json", post)):
            path = self.tmp / name
            path.write_text(document if isinstance(document, str) else json.dumps(document, indent=2), encoding="utf-8")
            paths.append(str(path))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *paths], capture_output=True, text=True, timeout=60)
        return result.returncode, result.stdout.splitlines()

    # RC-1
    def test_rc1_identical_findings(self):
        checks = {"a": ("fail", [("status_mismatch", "part_movement", 41), ("x", "work_order", "WO-1")], False)}
        self.assertEqual(self.compare(report(1, checks), report(1, checks)), (0, ["PRE-EXISTING 2 findings"]))

    # RC-2
    def test_rc2_new_identity(self):
        pre = report(1, {"a": ("fail", [("status_mismatch", "part_movement", 41)], False)})
        post = report(1, {"a": ("fail", [("status_mismatch", "part_movement", 41), ("status_mismatch", "part_movement", 42)], False)})
        self.assertEqual(self.compare(pre, post), (1, ["NEW a status_mismatch part_movement 42"]))
        # Same entity, another code: a new identity.
        post = report(1, {"a": ("fail", [("quantity_mismatch", "part_movement", 41)], False)})
        self.assertEqual(self.compare(pre, post), (1, ["NEW a quantity_mismatch part_movement 41"]))

    # RC-3
    def test_rc3_finding_gone(self):
        pre = report(1, {"a": ("fail", [("x", "part_movement", 1), ("x", "part_movement", 2)], False)})
        post = report(1, {"a": ("fail", [("x", "part_movement", 2)], False)})
        self.assertEqual(self.compare(pre, post), (0, ["PRE-EXISTING 1 findings"]))
        self.assertEqual(self.compare(pre, report(0, {"a": ("pass", [], False)})), (0, ["PRE-EXISTING 0 findings"]))

    # RC-4
    def test_rc4_check_only_in_post(self):
        pre = report(1, {"a": ("fail", [("x", "part_movement", 1)], False)})
        post = report(1, {"a": ("fail", [("x", "part_movement", 1)], False), "b": ("fail", [("y", "lot", 7)], False)})
        self.assertEqual(self.compare(pre, post), (1, ["NEW b y lot 7"]))

    # RC-5
    def test_rc5_post_check_error(self):
        pre = report(1, {"a": ("fail", [("x", "part_movement", 1)], False)})
        post = report(1, {"a": ("fail", [("x", "part_movement", 1)], False), "b": ("error", [], False)})
        code, lines = self.compare(pre, post)
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["CANNOT-COMPARE POST check b ended in error"])

    # RC-6
    def test_rc6_truncated_with_findings(self):
        truncated = report(1, {"a": ("fail", [("x", "part_movement", 1)], True)})
        complete = report(1, {"a": ("fail", [("x", "part_movement", 1)], False)})
        for pre, post, label in ((truncated, complete, "PRE"), (complete, truncated, "POST")):
            with self.subTest(truncated=label):
                code, lines = self.compare(pre, post)
                self.assertEqual(code, 2)
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith(f"CANNOT-COMPARE {label} check a is truncated"), lines)

    # RC-7
    def test_rc7_unusable_reports(self):
        good = report(1, {"a": ("fail", [("x", "part_movement", 1)], False)})
        cases = {
            "unparseable": ("{not json", good),
            "post exit_code 2": (good, report(2)),
            "pre exit_code 2": (report(2), good),
            "report_version": (dict(good, report_version=2), good),
        }
        for name, (pre, post) in cases.items():
            with self.subTest(case=name):
                code, lines = self.compare(pre, post)
                self.assertEqual(code, 2)
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("CANNOT-COMPARE "), lines)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
