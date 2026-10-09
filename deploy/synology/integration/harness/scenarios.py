"""Scenario dispatch (SPEC 6.2 order: setup -> L1 -> L2 -> L7 -> L3 -> cases -> L4 -> L6 -> ctl)."""
import os

from . import core
from . import fixture as fixture_module
from . import util

INSTALL_PHRASES = (r"INSTALL CONTROL r-[0-9a-f]{16}",)
TOOLS = (("docker", "/usr/local/bin/docker"), ("git", "/usr/bin/git"), ("ip", "/sbin/ip"),
         ("hostname", "/bin/hostname"))


def run(harness, name, *, rows=None):
    from . import loop2
    from . import loop3
    from . import loop7
    from . import loops
    from . import setup_scenario
    from . import cases
    from . import ctl
    from . import loop6
    from . import matrix
    from . import report
    function = {"setup": setup_scenario.run, "L1": loops.loop1, "L2": loop2.run, "L7": loop7.run,
                "L3": loop3.run, "L6": loop6.run, "ctl": ctl.run, "cases": lambda h: cases.run(h, rows), "report": report.run,
                "L4": lambda h: matrix.run(h, rows)}.get(name)
    if function is None:
        harness.log(f"scenario {name}: not implemented in this harness revision")
        return "not_run"
    return function(harness)


def lock_identity(path):
    info = os.stat(str(path))
    return f"{info.st_dev}:{info.st_ino}"


def setup_fixture(harness):
    if harness.fixture.get("commits"):
        return harness.fixture
    harness.log("fixture: upstream")
    value = fixture_module.build_upstream(harness.root, harness.work / "source.git")
    harness.fixture.update(value)
    harness.save()
    harness.log("fixture: base images")
    pins = harness.work / "base-images.json"
    harness.fixture["base_images"] = fixture_module.pin_base_images(pins if pins.exists() else None, harness.evidence)
    harness.save()
    return harness.fixture


def install(harness):
    if harness.fixture.get("install"):
        return harness.fixture["install"]
    commits = list(harness.fixture["commits"].values())
    fixture_module.write_settings(harness.settings_path, commits)
    stage = harness.root / "stage" / "control"
    record = fixture_module.stage_control(harness.work, stage, harness.settings_path)
    harness.evidence.write_json("control-stage.json", record)
    argv = ["/bin/sh", str(stage / "deploy" / "synology" / "install-control.sh"), "init", "--root", str(harness.pfroot),
            "--launcher-path", core.LAUNCHER]
    for tool, path in TOOLS:
        argv += ["--tool", f"{tool}={path}"]
    from . import terminal
    harness.log("install-control.sh init")
    result = terminal.run(argv, phrases=INSTALL_PHRASES, env=dict(core.PF_ENV), cwd="/", timeout=600)
    harness.evidence.append_log("transcripts/install-init.txt", f"$ {' '.join(argv)}\n{result.stdout}\n{result.stderr}\n"
                                                               f"exit {result.exit} aborted={result.aborted}\n")
    harness.record_step("setup-install-init", result="passed" if result.exit == 0 and not result.aborted else "failed",
                        extra={"argv": argv, "typed": result.typed, "exit": result.exit,
                               "stdout_excerpt": harness.evidence.excerpt(result.stdout),
                               "stderr_excerpt": harness.evidence.excerpt(result.stderr)})
    if result.exit != 0 or result.aborted:
        raise util.HarnessError("BLOCKED: install-control.sh init failed: " + result.stderr.strip()[-800:])
    locks = {}
    for path in sorted((harness.pfroot / "locks").glob("*.lock")) + sorted((harness.pfroot / "sources").glob("*.lock")):
        locks[str(path)] = lock_identity(path)
    installed = {"root": str(harness.pfroot), "launcher": core.LAUNCHER, "tools": dict(TOOLS), "locks": locks,
                 "release_files": {}}
    releases = harness.pfroot / "releases"
    for release in sorted(releases.iterdir()) if releases.is_dir() else []:
        for item in sorted(release.iterdir()):
            if item.is_file():
                import hashlib
                installed["release_files"][f"{release.name}/{item.name}"] = hashlib.sha256(item.read_bytes()).hexdigest()
    harness.fixture["install"] = installed
    harness.save()
    return installed


