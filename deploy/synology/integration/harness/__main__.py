"""``python3 -B -m harness`` (SPEC 6.2)."""
import argparse
import json
import os
import sys
import traceback

from . import core
from . import util

SCENARIO_ORDER = ("setup", "L1", "L2", "L7", "L3", "cases", "L4", "L6", "ctl")
SCENARIOS = SCENARIO_ORDER + ("L5", "audit-nf1", "report", "all")


def parser():
    result = argparse.ArgumentParser(prog="harness", allow_abbrev=False)
    result.add_argument("--root", required=True)
    result.add_argument("--evidence", required=True)
    result.add_argument("--run", default=os.environ.get("PFA34_RUN", "local"))
    result.add_argument("--scenario", action="append", choices=SCENARIOS, default=[])
    result.add_argument("--rows", default=None,
                        help="Comma-separated L4 row IDs (R01,B01,...), or case names for the cases scenario")
    result.add_argument("--stop-on-fail", action="store_true")
    result.add_argument("--dev-cmd", default=None, help=argparse.SUPPRESS)
    return result


def dev_command(harness, spec):
    """Development only: one launcher command with a dialogue; prints the transcript (never evidence)."""
    from . import terminal
    if spec.startswith("@"):  # a file copied in with `docker cp` (host argv never names an inner project)
        with open(spec[1:], encoding="utf-8") as handle:
            spec = handle.read()
    value = json.loads(spec)
    result = terminal.run(value["argv"], phrases=value.get("phrases", ()),
                          dialogue=[tuple(item) for item in value.get("dialogue", ())], env=dict(core.PF_ENV), cwd="/",
                          timeout=value.get("timeout", 3600))
    print("--- prompts", json.dumps(result.prompts, indent=1))
    print("--- typed", result.typed)
    print("--- stdout\n" + harness.evidence.redact(result.stdout))
    print("--- stderr\n" + harness.evidence.redact(result.stderr))
    print("--- exit", result.exit, "aborted", result.aborted)
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if os.geteuid() != 0:
        print("BLOCKED: the harness runs as uid 0 inside the dind container", file=sys.stderr)
        return 2
    harness = core.Harness(root=args.root, evidence_dir=args.evidence, run=args.run)
    if args.dev_cmd:
        return dev_command(harness, args.dev_cmd)
    from . import scenarios
    wanted = list(args.scenario) or ["all"]
    if "all" in wanted:
        wanted = list(SCENARIO_ORDER)
    wanted = [name for name in wanted if name != "report"]  # always regenerated at the end (finally)
    rows = [item.strip() for item in args.rows.split(",")] if args.rows else None
    harness.start_events()
    status = 0
    try:
        for name in wanted:
            harness.current_scenario = name
            try:
                outcome = scenarios.run(harness, name, rows=rows)
            except util.HarnessError as exc:
                text = str(exc)
                harness.log(f"scenario {name}: {text}")
                outcome = "blocked" if text.startswith("BLOCKED:") else "failed"
                harness.results["loops"].setdefault(name, {})["error"] = text
            except core.StepFailed as exc:
                harness.log(f"scenario {name}: step failed: {exc}")
                outcome = "failed"
                harness.results["loops"].setdefault(name, {})["error"] = str(exc)
            except Exception as exc:  # a harness defect: record it with its traceback, never as a product result
                harness.log(f"scenario {name}: harness error: {exc!r}\n{traceback.format_exc()}")
                outcome = "harness-error"
                harness.results["loops"].setdefault(name, {})["error"] = repr(exc)
            harness.results["loops"].setdefault(name, {})["outcome"] = outcome
            harness.save()
            harness.log(f"scenario {name}: {outcome}")
            if outcome == "blocked":
                status = max(status, 2) if name in ("setup", "L1") else max(status, 1)
                if name in ("setup", "L1"):
                    break
            elif outcome != "passed":
                status = max(status, 1)
                if args.stop_on_fail or name == "setup":
                    break
    finally:
        try:
            from . import report
            harness.current_scenario = "report"
            report.run(harness)
        except Exception as exc:  # the report is evidence bookkeeping; its failure is logged, never hidden
            harness.log(f"report generation failed: {exc!r}")
            status = max(status, 1)
        harness.stop_events()
        harness.save()
    return status


if __name__ == "__main__":
    sys.exit(main())
