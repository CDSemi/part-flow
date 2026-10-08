"""Compare a post-release reconciliation report with the pre-release report (P16-S3; OPERATIONS_RUNBOOK §5 step 9).

Usage: python3 deploy/production/reconcile_regression.py PRE.json POST.json

Both files are `python -m app.cli reconcile` JSON reports (report_version 1). A finding's identity is
(check id, code, entity type, entity id). Exit status, with one line per reason on stdout:
  0  no regression: every POST finding was already in PRE ("PRE-EXISTING <n> findings"; they stay open incidents)
  1  regression: a POST finding is absent from PRE ("NEW <check> <code> <type> <id>", one line each)
  2  could not compare ("CANNOT-COMPARE <reason>"): unreadable report, report_version not 1, exit_code above 1,
     a POST check with status error, or a truncated finding list (the complete list cannot be compared)
A check that exists only in POST is compared against an empty set. Standard library only (Python 3.8+), so it runs
on the host interpreter that OPERATIONS_RUNBOOK §7 already requires; release.sh calls it, never edits a report.
"""
import json
import sys


class CannotCompare(Exception):
    pass


def load(path, label):
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CannotCompare(f"{label} report {path} cannot be read as JSON ({type(exc).__name__})") from exc
    if not isinstance(report, dict) or report.get("report_version") != 1:
        raise CannotCompare(f"{label} report is not a reconcile report_version 1")
    exit_code = report.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code > 1 or exit_code < 0:
        raise CannotCompare(f"{label} report has exit_code {exit_code!r}: the run did not complete")
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise CannotCompare(f"{label} report has no check list")
    return checks


def identities(checks, label, reasons):
    found = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            raise CannotCompare(f"{label} report has a malformed check")
        check_id = check["id"]
        findings = check.get("findings") or []
        if not isinstance(findings, list):
            raise CannotCompare(f"{label} check {check_id} has a malformed finding list")
        if label == "POST" and check.get("status") == "error":
            reasons.append(f"POST check {check_id} ended in error")
        if check.get("truncated") is True and (findings or check.get("finding_count")):
            reasons.append(f"{label} check {check_id} is truncated (rerun reconcile with a larger --max-findings)")
        for finding in findings:
            entity = finding.get("entity") if isinstance(finding, dict) else None
            if not isinstance(entity, dict) or not isinstance(finding.get("code"), str):
                raise CannotCompare(f"{label} check {check_id} has a malformed finding")
            found.add((check_id, finding["code"], str(entity.get("type")), str(entity.get("id"))))
    return found


def compare(pre_path, post_path):
    """(exit status, output lines)."""
    try:
        pre_checks = load(pre_path, "PRE")
        post_checks = load(post_path, "POST")
        reasons = []
        pre = identities(pre_checks, "PRE", reasons)
        post = identities(post_checks, "POST", reasons)
    except CannotCompare as exc:
        return 2, [f"CANNOT-COMPARE {exc}"]
    if reasons:
        return 2, [f"CANNOT-COMPARE {reason}" for reason in reasons]
    new = sorted(post - pre)
    if new:
        return 1, ["NEW " + " ".join(identity) for identity in new]
    return 0, [f"PRE-EXISTING {len(post)} findings"]


def main(argv):
    if len(argv) != 2:
        print("Usage: python3 reconcile_regression.py PRE.json POST.json", file=sys.stderr)
        return 2
    status, lines = compare(argv[0], argv[1])
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
