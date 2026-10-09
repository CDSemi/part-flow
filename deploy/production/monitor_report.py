"""Turn a `status` or `reconcile` JSON report into the check lines of the monitoring scripts (P16-S6; OPERATIONS_RUNBOOK
§2, §7, §9).

Usage:
  python3 deploy/production/monitor_report.py status STATUS_JSON RC [--growth-file FILE]
  python3 deploy/production/monitor_report.py reconcile REPORT RC

`status` (deploy/production/check.sh): prints exactly four lines, `PASS|FAIL|SKIP <id> <reason>` for database, schema,
backup_age and archival_proposal, plus optional `NOTE` lines; exit 0 no FAIL, 1 a FAIL (an invalid report included),
2 usage error only. A report is valid only when it is one JSON document with report_version 1, command `status` and
an exit_code equal to RC (the process status; P16-S1 exit-code rule). With --growth-file, a database that answered adds
one line per UTC day (date, database bytes, Movement rows, Movement bytes) to FILE.

`reconcile` (deploy/production/scheduled-reconcile.sh): `RECONCILE clean|mismatch|error <path> (<n> findings, <ms> ms)`
and one `FAIL <check> <title>: ...` line per failing check; exit = the report's exit_code when it is valid, otherwise
`RECONCILE could_not_run ...` and exit 2. It never prints a finding's actual, expected or detail values (check (j)
findings may carry Worker badge values).

Standard library only (Python 3.8+): it runs on the host interpreter OPERATIONS_RUNBOOK §7 already requires.
"""
import datetime
import json
import os
import re
import sys

STATUS_IDS = ("database", "schema", "backup_age", "archival_proposal")
MAX_REASON = 300
GROWTH_HEADER = "date\tdatabase_bytes\tmovement_rows\tmovement_bytes\n"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class UsageError(Exception):
    pass


def clean(text):
    """One printable line of at most MAX_REASON characters."""
    text = _CONTROL.sub("?", str(text))
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 3] + "..."


def line(word, check_id, reason):
    return f"{word} {check_id} {clean(reason)}"


def load(path):
    """The one JSON document in PATH, or None (missing, empty, unreadable, not one object)."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def mib(value):
    return f"{value / 1048576:.1f}" if is_int(value) else "?"


def section(document, key):
    value = document.get(key)
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def valid_status(document, rc):
    return (
        document is not None
        and document.get("report_version") == 1
        and document.get("command") == "status"
        and is_int(document.get("exit_code"))
        and document.get("exit_code") == rc
    )


def status_lines(document, rc):
    """The four check lines of one status report (`document` None = unreadable)."""
    if not valid_status(document, rc):
        return [
            line("FAIL", "database", f"status could not run (exit {rc}); see status.err"),
            line("SKIP", "schema", "status did not run"),
            line("SKIP", "backup_age", "status did not run"),
            line("SKIP", "archival_proposal", "status did not run"),
        ]
    error = document.get("error") if isinstance(document.get("error"), dict) else None
    findings = [f for f in document.get("findings") or [] if isinstance(f, dict)]
    database = section(document, "database")
    movements = section(document, "movements")
    schema = section(document, "schema")
    backup = section(document, "backup")
    archival = section(document, "archival")
    lines = []

    if error is not None:
        lines.append(line("FAIL", "database", error.get("message") or error.get("code") or "status reported an error"))
    elif database.get("status") == "ok":
        locks = database.get("locks") if isinstance(database.get("locks"), dict) else {}
        lines.append(line(
            "PASS", "database",
            f"{database.get('name')} as {database.get('connected_role')}: {mib(database.get('size_bytes'))} MiB,"
            f" {database.get('connections')} connections, {locks.get('waiting')} lock waits"
            f" (longest {locks.get('longest_wait_seconds')} s), {movements.get('rows')} Movements"
            f" ({mib(movements.get('total_bytes'))} MiB)",
        ))
    else:
        lines.append(line("FAIL", "database", f"database status {database.get('status')}"))

    schema_findings = [f for f in findings if f.get("code") == "schema_not_ready"]
    if schema_findings:
        lines.append(line("FAIL", "schema", schema_findings[0].get("message") or "schema_not_ready"))
    elif schema.get("status") == "ok":
        lines.append(line("PASS", "schema", f"{schema.get('readiness')} at {schema.get('database_revision')}"))
    else:
        reason = (error or {}).get("code") or schema.get("status")
        lines.append(line("SKIP", "schema", f"not evaluated ({reason})"))

    backup_findings = [f for f in findings if str(f.get("code", "")).startswith("backup_")]
    if backup_findings:
        lines.append(line("FAIL", "backup_age", "; ".join(str(f.get("message") or f.get("code")) for f in backup_findings)))
    elif backup.get("status") == "ok":
        latest = backup.get("latest") if isinstance(backup.get("latest"), dict) else {}
        lines.append(line(
            "PASS", "backup_age",
            f"{latest.get('name')} completed {latest.get('age_hours')} h ago (limit {backup.get('max_age_hours')} h)",
        ))
    elif backup.get("status") == "not_applicable":
        lines.append(line("FAIL", "backup_age", "status ran without the backup directory"))
    else:
        lines.append(line("FAIL", "backup_age", f"backup status {backup.get('status')}"))

    archival_findings = [f for f in findings if str(f.get("code", "")).startswith("archival_")]
    if archival_findings:
        lines.append(line("FAIL", "archival_proposal",
                          "; ".join(str(f.get("message") or f.get("code")) for f in archival_findings)))
    elif archival.get("status") == "not_applicable":
        lines.append(line("SKIP", "archival_proposal", archival.get("reason") or "not applicable"))
    else:
        lines.append(line("PASS", "archival_proposal", f"archival status {archival.get('status')}"))
    return lines


def append_growth(path, document, today):
    """Append today's growth line when the database and the Movement count answered; a NOTE line on failure, else None."""
    if document is None or document.get("error") is not None:
        return None
    database = section(document, "database")
    movements = section(document, "movements")
    values = (database.get("size_bytes"), movements.get("rows"), movements.get("total_bytes"))
    if database.get("status") != "ok" or movements.get("status") != "ok" or not all(is_int(v) for v in values):
        return None
    try:
        last = ""
        exists = os.path.exists(path)
        if exists:
            with open(path, encoding="utf-8") as handle:
                for text in handle:
                    if text.strip():
                        last = text
        if last.split("\t", 1)[0] == today:
            return None
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            if not exists:
                handle.write(GROWTH_HEADER)
            handle.write(f"{today}\t{values[0]}\t{values[1]}\t{values[2]}\n")
    except OSError as exc:
        return f"NOTE growth file not updated: {clean(exc.strerror or type(exc).__name__)}"
    return None


def run_status(arguments):
    growth = None
    positional = []
    index = 0
    while index < len(arguments):
        if arguments[index] == "--growth-file":
            if index + 1 >= len(arguments) or growth is not None:
                raise UsageError("--growth-file needs one FILE")
            growth = arguments[index + 1]
            index += 2
            continue
        positional.append(arguments[index])
        index += 1
    if len(positional) != 2:
        raise UsageError("status needs STATUS_JSON RC")
    rc = parse_rc(positional[1])
    document = load(positional[0])
    lines = status_lines(document, rc)
    if growth is not None and valid_status(document, rc):
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        note = append_growth(growth, document, today)
        if note:
            lines.append(note)
    for text in lines:
        print(text)
    return 1 if any(text.startswith("FAIL ") for text in lines) else 0


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def run_reconcile(arguments):
    if len(arguments) != 2:
        raise UsageError("reconcile needs REPORT RC")
    path, rc = arguments[0], parse_rc(arguments[1])
    document = load(path)
    checks = document.get("checks") if document is not None else None
    if (
        document is None
        or document.get("report_version") != 1
        or document.get("command") != "reconcile"
        or not is_int(document.get("exit_code"))
        or document.get("exit_code") != rc
        or document.get("exit_code") not in (0, 1, 2)
        or document.get("result") not in ("clean", "mismatch", "error")
        or not isinstance(checks, list)
    ):
        print(clean(f"RECONCILE could_not_run {path} (exit {rc}; no complete report)"))
        return 2
    checks = [c for c in checks if isinstance(c, dict)]
    total = sum(c.get("finding_count") for c in checks if is_int(c.get("finding_count")))
    print(clean(f"RECONCILE {document['result']} {path} ({total} findings, {document.get('duration_ms')} ms)"))
    for check in checks:
        if check.get("status") == "fail":
            print(line("FAIL", check.get("id"), f"{check.get('title')}: {check.get('finding_count')} findings"))
        elif check.get("status") == "error":
            print(line("FAIL", check.get("id"), f"{check.get('title')}: {check.get('reason')}"))
    return document["exit_code"]


def parse_rc(text):
    if not re.fullmatch(r"[0-9]{1,3}", text or ""):
        raise UsageError(f"RC must be a process exit status, not {text!r}")
    return int(text)


def main(argv):
    try:
        if not argv or argv[0] not in ("status", "reconcile"):
            raise UsageError("the first argument must be status or reconcile")
        if argv[0] == "status":
            return run_status(argv[1:])
        return run_reconcile(argv[1:])
    except UsageError as exc:
        print(f"monitor_report: {exc}", file=sys.stderr)
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
