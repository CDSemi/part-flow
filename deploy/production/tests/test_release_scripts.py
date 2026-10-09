"""P16-S3: deploy/production/release.sh and smoke.sh against fake `docker`, `git`, `curl` and `sleep` executables
(P16-S3 SPEC section 6.3, cases RS-1..RS-24 and SS-1..SS-8; RS-25 covers Ctrl-C/TERM while a step runs; P16-S4 SPEC
section 6.4 adds RS-26, the preflight secret-file check).

A temporary directory holds a fake repository root (copies of both production Compose files and an env file) and a
`bin` directory placed first on PATH. Each fake is a POSIX `sh` wrapper that runs this interpreter (sys.executable) on
fake_tool.py, which appends its argv and the PARTFLOW_* variables of its environment to calls.jsonl and answers from
the case's rule table (exit code, stdout, stderr; for curl the HTTP status, headers and body); an answer may also ask
the wrapper to send a signal to the calling script once it has answered. A `python3` wrapper of
this interpreter is placed there too, so the real reconcile_regression.py runs under `python3` on every host (the
Windows Store alias is not an interpreter). The scripts run under the real `sh` (dash on the CI runner); a missing `sh`
fails the run, never skips it. Nothing touches Docker, git or the network.

Run with the other production tests:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
RELEASE_SH = REPO / "deploy" / "production" / "release.sh"
SMOKE_SH = REPO / "deploy" / "production" / "smoke.sh"
SH_REQUIRED = "a POSIX sh is required for the release script tests"

CURRENT = "v1.0.0"
TAG = "v1.1.0"
HEAD = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
PORT = "18080"
DB_REVISION = "0032_phase14_route_adjusted"
NEW_REVISION = "0033_s3_test"
BACKUP_REF = "pre-release dump 2026-10-08T10:00Z"
COMPOSE_PREFIX = "compose -f compose.production.yaml --env-file .env.production "
HOST_VARIABLE_PREFIXES = ("PARTFLOW_", "POSTGRES_", "COMPOSE_")

FAKE_TOOL = r'''
import json, os, re, sys

state_dir, tool, args = sys.argv[1], sys.argv[2], sys.argv[3:]


def load(name, default):
    try:
        with open(os.path.join(state_dir, name), encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def curl_request(args):
    method, headers, options, url, i = "GET", [], {}, None, 0
    while i < len(args):
        a = args[i]
        if a in ("-X", "-H", "-o", "-D", "-w", "--data", "--max-time"):
            if a == "-X":
                method = args[i + 1]
            elif a == "-H":
                headers.append(args[i + 1])
            else:
                options[a] = args[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if a.startswith("-"):
            options.setdefault("flags", "")
            options["flags"] += a[1:]
            i += 1
            continue
        url = a
        i += 1
    rest = url.split("://", 1)[1]
    path = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    request = " ".join([method, path] + ["[" + h + "]" for h in headers])
    return request, options


request, options = None, {}
subject = " ".join(args)
if tool == "curl":
    request, options = curl_request(args)
    subject = request
env = {k: v for k, v in os.environ.items() if k.startswith("PARTFLOW_")}
with open(os.path.join(state_dir, "calls.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps({"tool": tool, "argv": args, "request": request, "env": env}) + "\n")

rules = load("rules.json", {}).get(tool, [])
counts = load("counts.json", {})
answer = {}
for index, rule in enumerate(rules):
    if not re.search(rule["match"], subject):
        continue
    if rule.get("not_match") and re.search(rule["not_match"], subject):
        continue
    if any(os.environ.get(k) != v for k, v in (rule.get("env") or {}).items()):
        continue
    key = tool + ":" + str(index)
    seen = counts.get(key, 0)
    counts[key] = seen + 1
    with open(os.path.join(state_dir, "counts.json"), "w", encoding="utf-8") as handle:
        json.dump(counts, handle)
    responses = rule["responses"]
    answer = responses[min(seen, len(responses) - 1)]
    break

if tool == "curl":
    if answer.get("rc"):
        sys.stderr.write("curl: (7) Failed to connect\n")
        sys.exit(answer["rc"])
    status = answer.get("status", 404)
    body = answer.get("body", "")
    if "-D" in options:
        with open(options["-D"], "w", encoding="utf-8", newline="") as handle:
            handle.write("HTTP/1.1 %d Fake\r\n" % status)
            for name, value in (answer.get("headers") or {}).items():
                handle.write("%s: %s\r\n" % (name, value))
            handle.write("\r\n")
    if "f" in options.get("flags", "") and status >= 400:
        sys.stderr.write("curl: (22) The requested URL returned error: %d\n" % status)
        sys.exit(22)
    if "-o" in options:
        with open(options["-o"], "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
    else:
        sys.stdout.write(body)
    if "-w" in options:
        sys.stdout.write(options["-w"].replace("%{http_code}", "%03d" % status))
    sys.exit(0)

if answer.get("signal"):
    with open(os.path.join(state_dir, "signal"), "w", encoding="utf-8") as handle:
        handle.write(answer["signal"])
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("rc", 0))
'''


# ---------------------------------------------------------------------------
# Rule and document builders
# ---------------------------------------------------------------------------


def rule(match, *responses, env=None, not_match=None):
    entry = {"match": match, "responses": list(responses) or [{}]}
    if env is not None:
        entry["env"] = env
    if not_match is not None:
        entry["not_match"] = not_match
    return entry


def out(stdout="", rc=0, stderr="", signal=None):
    """A docker/git answer; `signal` (TERM, INT) is sent to the calling script once the fake has answered."""
    answer = {"stdout": stdout, "rc": rc, "stderr": stderr}
    if signal is not None:
        answer["signal"] = signal
    return answer


def http(status, body="", **headers):
    return {"status": status, "body": body, "headers": {k.replace("_", "-"): v for k, v in headers.items()}}


def revision_doc(state, exit_code, database=DB_REVISION, expected=DB_REVISION, readiness="current", accepted=None, pending=(),
                 release=TAG, commit=HEAD):
    return json.dumps(
        {
            "report_version": 1, "command": "revision", "state": state, "exit_code": exit_code, "release": release,
            "commit": commit, "expected_revision": expected, "database_revision": database, "accepted_revision": accepted,
            "override_ignored": False, "readiness": readiness, "pending_revisions": list(pending),
            "non_transactional_revisions": [], "error": None,
        },
        indent=2, ensure_ascii=True,
    ) + "\n"


def reconcile_doc(exit_code, findings=(), truncated=False):
    """A reconcile report; `findings` = [(check id, code, entity type, entity id)]."""
    checks = {}
    for check_id, code, kind, ident in findings:
        checks.setdefault(check_id, []).append({"code": code, "entity": {"type": kind, "id": ident}, "part_number": None,
                                                "expected": None, "actual": None, "detail": {}})
    document = {
        "report_version": 1, "command": "reconcile", "result": {0: "clean", 1: "mismatch", 2: "error"}[exit_code],
        "exit_code": exit_code, "started_at": "2026-10-08T10:00:00Z", "finished_at": "2026-10-08T10:00:01Z",
        "duration_ms": 1000, "runtime": {}, "database": {}, "options": {}, "error": None,
        "checks": [
            {"id": check_id, "title": check_id, "status": "fail", "duration_ms": 1, "examined": {},
             "finding_count": len(items), "truncated": truncated, "reason": None, "error_code": None, "findings": items}
            for check_id, items in checks.items()
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def migrate_doc(result, exit_code, before=DB_REVISION, after=None, applied=(), error_code=None, backup_ref=BACKUP_REF):
    after = after if after is not None else before
    return json.dumps(
        {
            "report_version": 1, "command": "migrate", "result": result, "exit_code": exit_code,
            "started_at": "2026-10-08T10:00:00Z", "finished_at": "2026-10-08T10:00:01Z", "duration_ms": 1000,
            "release": TAG, "commit": HEAD, "expected_revision": NEW_REVISION, "revision_before": before,
            "revision_after": after, "applied_revisions": list(applied),
            "backup": {"kind": "reference", "reference": backup_ref, "verified": False,
                       "verification": "pending: backup-verify arrives with P16-S5"},
            "grants": None if error_code else {
                "status": "applied", "roles": {"application": "partflow_app", "maintenance": "partflow_maintenance"},
                "tables": 29, "sequences": 23, "default_privileges_removed": 0, "foreign_grantees": [],
            },
            "error": {"code": error_code, "message": "refused"} if error_code else None,
        },
        indent=2, ensure_ascii=True,
    ) + "\n"


# The top-level secrets of compose.production.yaml (P16-S4); the fake `config --format json` names a file for each.
SECRET_NAMES = ("postgres_password", "partflow_app_password", "partflow_maintenance_password")
# Without `--profile ops` real Compose leaves out a secret only profile-gated services use: the maintenance password
# file is mounted by db-roles alone, so the unprofiled model names just these two.
UNPROFILED_SECRET_NAMES = ("postgres_password", "partflow_app_password")
SECRET_ERROR = ("is missing, empty or not a regular file. Create it as DEPLOYMENT §3.1 describes; if Compose already"
                " created a directory there, remove it first. Nothing was changed.")


def compose_config_doc(secrets_dir, names=SECRET_NAMES):
    """`docker compose config --format json` as Compose prints it (two-space indent); services also name secrets."""
    return json.dumps(
        {
            "name": "partflow-production",
            "networks": {"internal": {"name": "partflow-production_internal", "internal": True}},
            "secrets": {name: {"name": "partflow-production_" + name, "file": (Path(secrets_dir) / name).as_posix()}
                        for name in names},
            "services": {"backend": {"image": "partflow/backend:" + CURRENT,
                                     "secrets": [{"source": "partflow_app_password", "file": "/not/a/top/level/secret"}]}},
        },
        indent=2,
    ) + "\n"


CURRENT_ENV = {"PARTFLOW_RELEASE": None}
CANDIDATE_ENV = {"PARTFLOW_RELEASE": TAG}
REVISION = r"run --rm --no-deps -T backend python -m app\.cli revision$"
RECONCILE = r"run --rm --no-deps -T backend python -m app\.cli reconcile --max-findings 10000$"
CHECK_J = r"run --rm --no-deps -T backend python -m app\.cli reconcile --check j$"
MIGRATE = r"--profile ops run --rm -T migrate "
LABEL_INSPECT = r"^image inspect --format \{\{index \.Config\.Labels \"org\.opencontainers\.image\.revision\"\}\} partflow/(backend|web):"
VERSION_INSPECT = r"^image inspect --format \{\{index \.Config\.Labels \"org\.opencontainers\.image\.version\"\}\} partflow/(backend|web):"
GIT_STATUS = "status --porcelain --untracked-files=all -- backend frontend compose.production.yaml compose.production.build.yaml deploy/production"
GIT_IGNORED = "ls-files --others --ignored --exclude-standard -- frontend :!frontend/node_modules :!frontend/dist :!frontend/coverage"


def health_body(release=TAG, schema="current"):
    return json.dumps({"status": "ok", "service": "partflow-api", "database": "connected", "release": release,
                       "commit": HEAD, "schema": schema}, separators=(",", ":"))


def shell_body(release=TAG):
    meta = "" if release is None else f'<meta name="partflow-release" content="{release}">'
    return f'<!doctype html><html><head>{meta}</head><body><div id="root"></div></body></html>'


def default_rules():
    """The happy path: production mode, migration 0032 -> 0033 pending, every check clean."""
    return {
        "docker": [
            rule(r"config --quiet$"),
            rule(r"^ps -a --format", out("partflow-production\npartflow\n")),
            rule(r"^image inspect --format \{\{\.Id\}\} partflow/backend:" + re.escape(CURRENT) + "$", out("sha256:cur-backend\n")),
            rule(r"^image inspect --format \{\{\.Id\}\} partflow/web:" + re.escape(CURRENT) + "$", out("sha256:cur-web\n")),
            rule(LABEL_INSPECT, out("", rc=1, stderr="Error: No such image\n")),
            rule(r"^image inspect --format \{\{\.Id\}\} partflow/backend:" + re.escape(TAG) + "$", out("sha256:new-backend\n")),
            rule(r"^image inspect --format \{\{\.Id\}\} partflow/web:" + re.escape(TAG) + "$", out("sha256:new-web\n")),
            rule(r"-f compose\.production\.build\.yaml build backend web$"),
            rule(REVISION, out(revision_doc("current", 0)), env=CURRENT_ENV),
            rule(REVISION, out(revision_doc("upgrade_available", 1, expected=NEW_REVISION, readiness="mismatch",
                                            pending=[NEW_REVISION]), rc=1), env=CANDIDATE_ENV),
            rule(RECONCILE, out(reconcile_doc(0))),
            rule(CHECK_J, out(reconcile_doc(0))),
            rule(MIGRATE, out(migrate_doc("upgraded", 0, after=NEW_REVISION, applied=[NEW_REVISION]))),
            rule(r"stop backend$"),
            rule(r"ps --status running -q backend$", out("")),
            rule(r"ps -q backend$", out("cid-backend\n")),
            rule(r"ps -q web$", out("cid-web\n")),
            rule(r"^inspect --format \{\{\.Image\}\} cid-backend$", out("sha256:new-backend\n")),
            rule(r"^inspect --format \{\{\.Image\}\} cid-web$", out("sha256:new-web\n")),
            rule(r"up -d"),
        ],
        "git": [
            rule(r"^rev-parse HEAD$", out(HEAD + "\n")),
            rule(r"^status --porcelain", out("")),
            rule(r"^ls-files --others --ignored", out("")),
            rule(r"^rev-parse -q --verify refs/tags/", out(HEAD + "\n")),
        ],
        "curl": [
            rule(r"^GET /api/health$", http(200, health_body(), content_type="application/json")),
            rule(r"^GET /api/health/live$", http(200, json.dumps({"status": "live", "service": "partflow-api", "release": TAG, "commit": HEAD}, separators=(",", ":")), content_type="application/json")),
            rule(r"^GET /$", http(200, shell_body(), content_type="text/html", cache_control="no-cache")),
            rule(r"^GET /management/work-orders$", http(200, shell_body(), content_type="text/html", cache_control="no-cache")),
            rule(r"^GET /api/partflow-smoke-missing$", http(404, '{"detail":"Not Found"}', content_type="application/json")),
            rule(r"^POST /api/partflow-smoke-gate .*\[X-PartFlow-Release: " + re.escape(TAG) + r"\]", http(404, '{"detail":"Not Found"}', content_type="application/json")),
            rule(r"^POST /api/partflow-smoke-gate", http(409, '{"detail":"PartFlow was updated","release_mismatch":true}', content_type="application/json"),
                 not_match=r"X-PartFlow-Release"),
        ],
        "sleep": [rule(r".*")],
    }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def find_sh():
    return shutil.which("sh")


class Harness(unittest.TestCase):
    env_lines = (
        f"PARTFLOW_RELEASE={CURRENT}",
        "PARTFLOW_SECRETS_DIR=/srv/partflow/secrets",
        "PARTFLOW_SITE_TIMEZONE=UTC",
        f"PARTFLOW_HTTP_PORT={PORT}",
        "POSTGRES_USER=partflow_owner",
    )

    def setUp(self):
        self.sh = find_sh()
        if self.sh is None:
            raise AssertionError(SH_REQUIRED)
        self._tmp = tempfile.TemporaryDirectory(prefix="pf-s3-scripts-")
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "checkout"
        self.root.mkdir()
        for name in ("compose.production.yaml", "compose.production.build.yaml"):
            shutil.copyfile(REPO / name, self.root / name)
        self.env_file = self.root / ".env.production"
        self.write_env_file(self.env_lines)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.records = self.tmp / "records"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.tmp / "fake_tool.py"
        fake.write_text(FAKE_TOOL, encoding="utf-8")
        python = Path(sys.executable).as_posix()
        state = self.state.as_posix()
        for tool in ("docker", "git", "curl", "sleep"):
            self.wrapper(tool, f'"{python}" "{fake.as_posix()}" "{state}" {tool} "$@"\nrc=$?\n'
                               f'if [ -f "{state}/signal" ]; then\n'
                               f'    signal=$(cat "{state}/signal"); rm -f "{state}/signal"; kill -s "$signal" "$PPID"\n'
                               f'fi\nexit $rc')
        self.wrapper("python3", f'exec "{python}" "$@"')
        self.rules = default_rules()
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        for name in SECRET_NAMES:
            (self.secrets / name).write_text(f"rs-{name}-value\n", encoding="utf-8")
        self.rules["docker"][1:1] = [
            rule(r"--profile ops config --format json$", out(compose_config_doc(self.secrets))),
            rule(r"config --format json$", out(compose_config_doc(self.secrets, names=UNPROFILED_SECRET_NAMES))),
        ]

    def tearDown(self):
        self._tmp.cleanup()

    def wrapper(self, name, line):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8", newline="\n")
        os.chmod(path, 0o755)

    def write_env_file(self, lines):
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def env_text(self):
        return self.env_file.read_text(encoding="utf-8")

    def prepend(self, tool, *rules):
        self.rules[tool][0:0] = list(rules)

    def environment(self):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(HOST_VARIABLE_PREFIXES)}
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["HOME"] = self.tmp.as_posix()
        return env

    def run_script(self, script, *arguments):
        (self.state / "rules.json").write_text(json.dumps(self.rules), encoding="utf-8")
        result = subprocess.run(
            [self.sh, script.as_posix(), *arguments], cwd=self.root, env=self.environment(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        self.result = result
        return result

    def release(self, *extra, backup=("--pre-release-backup", BACKUP_REF), release=TAG):
        arguments = ["--release", release, "--operator", "Ops Person", "--approver", "Owner Person", *backup,
                     "--records-dir", self.records.as_posix(), *extra]
        return self.run_script(RELEASE_SH, *arguments)

    def calls(self, tool=None):
        path = self.state / "calls.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [e for e in entries if tool is None or e["tool"] == tool]

    def docker(self):
        """The docker invocations without the common `compose -f … --env-file …` prefix."""
        labels = []
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            labels.append(text[len(COMPOSE_PREFIX):] if text.startswith(COMPOSE_PREFIX) else text)
        return labels

    def docker_entries(self, pattern):
        return [e for e in self.calls("docker") if re.search(pattern, " ".join(e["argv"]))]

    def record_path(self):
        found = sorted(self.records.glob("*-*/record.json"))
        self.assertEqual(len(found), 1, f"record.json files: {found}\n{self.result.stdout}\n{self.result.stderr}")
        return found[0]

    def record(self):
        return json.loads(self.record_path().read_text(encoding="utf-8"))

    def record_dir(self):
        return self.record_path().parent

    def reset(self):
        """A fresh fixture for the next subTest of one test method (the framework's tearDown cleans the last)."""
        self.tearDown()
        self.setUp()

    def assertExit(self, code, outcome=None):
        self.assertEqual(self.result.returncode, code, f"stdout:\n{self.result.stdout}\nstderr:\n{self.result.stderr}")
        if outcome is not None:
            self.assertEqual(self.record()["outcome"], outcome)

    def assertNoDocker(self, *patterns):
        for pattern in patterns:
            self.assertFalse([c for c in self.docker() if re.search(pattern, c)], f"unexpected docker {pattern}: {self.docker()}")

    def assertSafeInvocations(self):
        """RS-24: the build file only in the build; -T on every JSON-reading run; nothing removed or re-tagged."""
        docker = self.docker()
        with_build_file = [c for c in docker if "compose.production.build.yaml" in c]
        self.assertEqual(len(with_build_file), len([c for c in docker if re.search(r" build backend web$", c)]))
        self.assertLessEqual(len(with_build_file), 1)
        for c in with_build_file:
            self.assertTrue(c.endswith("-f compose.production.build.yaml build backend web"), c)
        for c in docker:
            if re.search(r"app\.cli (revision|reconcile)| -T migrate| run .*migrate", c):
                self.assertIn(" -T ", f" {c} ", c)
            words = c.split()
            self.assertNotIn("rmi", words, c)
            self.assertNotIn("prune", words, c)
            self.assertFalse(c.startswith(("image rm", "tag ", "image tag")), c)


# The docker sequence of a full release with a pending migration (RS-1).
CURRENT_IMAGES = [
    "config --quiet",
    "--profile ops config --format json",
    'ps -a --format {{.Label "com.docker.compose.project"}}',
    f"image inspect --format {{{{.Id}}}} partflow/backend:{CURRENT}",
    f"image inspect --format {{{{.Id}}}} partflow/web:{CURRENT}",
]
CANDIDATE_IMAGES = [
    f'image inspect --format {{{{index .Config.Labels "org.opencontainers.image.revision"}}}} partflow/backend:{TAG}',
    f'image inspect --format {{{{index .Config.Labels "org.opencontainers.image.revision"}}}} partflow/web:{TAG}',
]
CANDIDATE_IDS = [
    f"image inspect --format {{{{.Id}}}} partflow/backend:{TAG}",
    f"image inspect --format {{{{.Id}}}} partflow/web:{TAG}",
]
RUN_REVISION = "run --rm --no-deps -T backend python -m app.cli revision"
RUN_RECONCILE = "run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000"
RUN_CHECK_J = "run --rm --no-deps -T backend python -m app.cli reconcile --check j"
BUILD = "-f compose.production.build.yaml build backend web"
SMOKE_DOCKER = [
    "ps -q backend", "inspect --format {{.Image}} cid-backend", f"image inspect --format {{{{.Id}}}} partflow/backend:{TAG}",
    "ps -q web", "inspect --format {{.Image}} cid-web", f"image inspect --format {{{{.Id}}}} partflow/web:{TAG}",
]
FULL_PENDING = [
    *CURRENT_IMAGES, RUN_REVISION, RUN_RECONCILE, *CANDIDATE_IMAGES, BUILD, *CANDIDATE_IDS, RUN_CHECK_J, RUN_REVISION,
    "stop backend", "ps --status running -q backend", f"--profile ops run --rm -T migrate --pre-release-backup {BACKUP_REF}",
    RUN_RECONCILE, "up -d --no-deps backend", "up -d --no-deps web", *SMOKE_DOCKER,
]


def nothing_pending(harness):
    harness.prepend("docker",
                    rule(REVISION, out(revision_doc("current", 0, expected=DB_REVISION)), env=CANDIDATE_ENV),
                    rule(MIGRATE, out(migrate_doc("already_current", 0))))


# ---------------------------------------------------------------------------
# release.sh
# ---------------------------------------------------------------------------


class ReleaseFlow(Harness):
    # RS-1, RS-14, RS-24
    def test_rs1_full_release_with_a_pending_migration(self):
        self.release("--environment", "production", "--url", "https://partflow.example.lan")
        self.assertExit(0, "completed")
        self.assertEqual(self.docker(), FULL_PENDING)
        build = self.docker_entries(r" build backend web$")[0]
        self.assertEqual(build["env"], {"PARTFLOW_RELEASE": TAG, "PARTFLOW_COMMIT": HEAD})
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            if "app.cli" in text or "migrate" in text:
                candidate = entry["env"].get("PARTFLOW_RELEASE") == TAG
                self.assertNotIn("PARTFLOW_COMMIT", entry["env"], text)
                if not candidate:
                    self.assertNotIn("PARTFLOW_RELEASE", entry["env"], text)
        candidate_runs = [" ".join(e["argv"][-4:]) for e in self.calls("docker") if e["env"].get("PARTFLOW_RELEASE") == TAG]
        self.assertEqual(len(candidate_runs), 5, candidate_runs)  # build, check j, revision, migrate, post reconcile
        env = self.env_text()
        self.assertIn(f"PARTFLOW_RELEASE={TAG}\n", env)
        self.assertIn("PARTFLOW_ACCEPT_SCHEMA_REVISION=\n", env)
        self.assertIn(f"PARTFLOW_HTTP_PORT={PORT}\n", env)
        self.assertEqual((self.record_dir() / "env-before.txt").read_text(encoding="utf-8"), "\n".join(self.env_lines) + "\n")
        record = self.record()
        self.assertTrue(record["writes_reopened_at"])
        self.assertIs(record["refrozen"], False)
        self.assertEqual(record["migration"], {"result": "upgraded", "file": "migrate.json", "log": "migrate.log"})
        self.assertEqual(record["alembic"], {"before": DB_REVISION, "after": NEW_REVISION, "expected": NEW_REVISION})
        self.assertEqual(record["backup"]["reference"], BACKUP_REF)
        self.assertEqual(record["backup"]["verification"], "pending: backup-verify arrives with P16-S5")
        self.assertEqual(record["smoke"], {"file": "smoke.txt", "exit_code": 0})
        self.assertIn("PASS S-8", (self.record_dir() / "smoke.txt").read_text(encoding="utf-8"))
        self.assertSafeInvocations()

    # RS-14
    def test_rs14_record_fields(self):
        self.release("--rollback-deadline", "2026-10-09 18:00", "--observation-owner", "Shift lead",
                     "--known-limitations", "none")
        self.assertExit(0)
        record = self.record()
        for key in ("record_version", "environment", "url", "host", "release", "alembic", "operator", "approver",
                    "started_at", "finished_at", "backup", "migration", "steps", "reconcile", "writes_reopened_at",
                    "refrozen", "smoke", "rollback_deadline", "observation_owner", "known_limitations", "rehearsal",
                    "outcome"):
            self.assertIn(key, record)
        self.assertEqual(record["record_version"], 1)
        self.assertEqual(record["environment"], "production")
        self.assertIsNone(record["url"])
        self.assertEqual(record["release"], {
            "tag": TAG, "commit": HEAD, "previous_tag": CURRENT, "git_tag_checked": True,
            "images": {"backend": "sha256:new-backend", "web": "sha256:new-web"},
        })
        self.assertEqual((record["operator"], record["approver"]), ("Ops Person", "Owner Person"))
        self.assertRegex(record["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(record["reconcile"], {
            "pre": {"file": "pre-reconcile.json", "exit_code": 0},
            "candidate_j": {"file": "candidate-j.json", "exit_code": 0},
            "post": {"file": "post-reconcile.json", "exit_code": 0},
            "baseline_image": "current", "skip_reason": None, "regression": None,
        })
        self.assertEqual(
            [s["name"] for s in record["steps"]],
            ["preflight", "current_revision", "pre_reconcile", "build", "candidate_identity_check", "candidate_revision",
             "freeze", "migrate", "post_reconcile", "switch_backend", "health_wait", "switch_web", "smoke", "record"],
        )
        for step in record["steps"]:
            self.assertEqual(set(step), {"name", "exit_code", "started_at", "finished_at", "output"})
        self.assertEqual((record["rollback_deadline"], record["observation_owner"], record["known_limitations"]),
                         ("2026-10-09 18:00", "Shift lead", "none"))
        self.assertIs(record["rehearsal"], False)
        if os.name != "nt":
            self.assertEqual(self.record_dir().stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.records / ".release.lock").exists())

    # RS-2, RS-24
    def test_rs2_nothing_pending(self):
        nothing_pending(self)
        self.release()
        self.assertExit(0, "completed")
        self.assertNoDocker(r"^stop backend$", r"ps --status running")
        self.assertEqual(len(self.docker_entries(MIGRATE)), 1)
        self.assertEqual(self.record()["migration"]["result"], "already_current")
        self.assertIn(f"PARTFLOW_RELEASE={TAG}\n", self.env_text())
        self.assertSafeInvocations()

    # RS-3
    def test_rs3_check_j_fails(self):
        self.prepend("docker", rule(CHECK_J, out(reconcile_doc(1), rc=1)))
        self.release()
        self.assertExit(1, "stopped_unchanged")
        self.assertNoDocker(r"stop backend", MIGRATE, r"^up ")
        self.assertEqual(self.env_text(), "\n".join(self.env_lines) + "\n")

    # RS-4
    def test_rs4_migrate_refused_while_frozen_reopens(self):
        self.prepend("docker", rule(MIGRATE, out(migrate_doc("refused", 1, error_code="revision_unknown"), rc=1)))
        self.release()
        self.assertExit(1, "aborted_reopened")
        docker = self.docker()
        self.assertEqual(docker[-2:], [RUN_REVISION, "up -d backend"])
        self.assertIsNone(self.docker_entries(r"app\.cli revision$")[-1]["env"].get("PARTFLOW_RELEASE"))
        self.assertNoDocker(r"--no-deps backend$", r"--no-deps web$")
        self.assertEqual(self.env_text(), "\n".join(self.env_lines) + "\n")

    def test_rs4_migrate_refused_reopen_check_fails_stays_frozen(self):
        self.prepend("docker", rule(MIGRATE, out(migrate_doc("failed", 2, error_code="migration_failed"), rc=2)))
        self.prepend("docker", rule(REVISION, out(revision_doc("current", 0)), out(revision_doc("upgrade_available", 1, readiness="mismatch"), rc=1), env=CURRENT_ENV))
        self.release()
        self.assertExit(3, "stopped_frozen")
        self.assertNoDocker(r"^up ")

    # RS-5
    def test_rs5_migrate_outcome_unknown(self):
        self.prepend("docker", rule(MIGRATE, out(migrate_doc("outcome_unknown", 2, error_code="outcome_unknown"), rc=2)))
        self.release()
        self.assertExit(3, "stopped_frozen")
        self.assertNoDocker(r"^up ")
        self.assertEqual(self.env_text(), "\n".join(self.env_lines) + "\n")
        self.assertIn("OPERATIONS_RUNBOOK §6", self.result.stderr)

    def test_rs5_migrate_invalid_output_stays_frozen(self):
        self.prepend("docker", rule(MIGRATE, out("usage: app.cli migrate ...\n", rc=2)))
        self.release()
        self.assertExit(3, "stopped_frozen")
        self.assertNoDocker(r"^up ")

    # RS-6
    def test_rs6_post_reconcile_findings_after_a_migration(self):
        self.prepend("docker", rule(RECONCILE, out(reconcile_doc(0)), out(reconcile_doc(1, [("a", "x", "part_movement", 1)]), rc=1)))
        self.release()
        self.assertExit(3, "stopped_frozen")
        self.assertNoDocker(r"^up ")
        self.assertIn("pre-reconcile.json", self.result.stderr)
        self.assertIn("post-reconcile.json", self.result.stderr)
        self.assertIn("up -d backend", self.result.stderr)
        self.assertEqual(self.record()["reconcile"]["post"], {"file": "post-reconcile.json", "exit_code": 1})

    # RS-9
    def test_rs9_release_equals_current(self):
        self.release(release=CURRENT)
        self.assertEqual(self.result.returncode, 1, self.result.stderr)
        self.assertEqual(self.calls(), [])

    # RS-13
    def test_rs13_pre_reconcile_findings_block_without_acceptance(self):
        self.prepend("docker", rule(RECONCILE, out(reconcile_doc(1, [("a", "x", "part_movement", 1)]), rc=1)))
        self.release()
        self.assertExit(1, "stopped_unchanged")
        self.assertNoDocker(r" build ", r"stop backend", MIGRATE)

    def test_rs13_pre_reconcile_findings_accepted(self):
        self.prepend("docker", rule(RECONCILE, out(reconcile_doc(1, [("a", "x", "part_movement", 1)]), rc=1), out(reconcile_doc(0))))
        self.release("--accept-pre-release-findings")
        self.assertExit(0, "completed")
        self.assertIsNone(self.record()["reconcile"]["regression"])

    # RS-15
    def test_rs15_lock_held(self):
        (self.records / ".release.lock").mkdir(parents=True)
        self.release()
        self.assertEqual(self.result.returncode, 1, self.result.stderr)
        self.assertIn(".release.lock", self.result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.records / ".release.lock").is_dir())

    # RS-16
    def test_rs16_reconcile_report_without_its_exit_code_line(self):
        self.prepend("docker", rule(RECONCILE, out(reconcile_doc(1), rc=0)))
        self.release()
        self.assertExit(1, "stopped_unchanged")
        self.assertNoDocker(r" build ")

    # RS-23
    def test_rs23_current_images_missing(self):
        for service in ("backend", "web"):
            with self.subTest(service=service):
                self.reset()
                self.prepend("docker", rule(r"^image inspect --format \{\{\.Id\}\} partflow/" + service + ":" + re.escape(CURRENT) + "$",
                                            out("", rc=1, stderr="Error: No such image\n")))
                self.release()
                self.assertExit(1, "stopped_unchanged")
                self.assertIn(f"The images of the running release {CURRENT} are not on this host, so rollback path 1 would be"
                              " impossible. Restore them before releasing (OPERATIONS_RUNBOOK §5, Before maintenance).",
                              self.result.stderr)
                self.assertNoDocker(r"^run ", r"--profile ops run", r" build ", r"^stop ", r"^up ")


class ReleasePreflight(Harness):
    # RS-26 (P16-S4): every top-level secret file is a regular, non-empty file before anything runs.
    def test_rs26_secret_files(self):
        def missing(path):
            path.unlink()

        def empty(path):
            path.write_text("", encoding="utf-8")

        def directory(path):
            path.unlink()
            path.mkdir()

        # partflow_maintenance_password is named only by the ops-profile model (db-roles), so its absence must stop
        # step 0 too: a later `--profile ops run db-roles` would otherwise leave a directory at its path.
        for name, damage, secret_name in (("missing", missing, "partflow_app_password"),
                                          ("empty", empty, "partflow_app_password"),
                                          ("directory", directory, "partflow_app_password"),
                                          ("maintenance missing", missing, "partflow_maintenance_password")):
            with self.subTest(case=name):
                self.reset()
                secret = self.secrets / secret_name
                damage(secret)
                self.release()
                self.assertExit(1, "stopped_unchanged")
                self.assertIn(f"the secret file {secret_name} ({secret.as_posix()}) {SECRET_ERROR}", self.result.stderr)
                self.assertEqual(self.docker(), CURRENT_IMAGES[:2])
                self.assertNoDocker(r"^run ", r"--profile ops run", r" build ", r"^stop ", r"^up ")
                steps = self.record()["steps"]
                self.assertEqual([(s["name"], s["exit_code"]) for s in steps], [("preflight", 1)])
                self.assertEqual(self.calls("git"), [])
                if name == "directory":
                    self.assertTrue(secret.is_dir())
                    self.assertEqual(list(secret.iterdir()), [])
        with self.subTest(case="all three regular files"):
            self.reset()
            self.release()
            self.assertExit(0, "completed")
            self.assertEqual(self.docker(), FULL_PENDING)
            preflight = (self.record_dir() / "preflight.log").read_text(encoding="utf-8")
            for name in SECRET_NAMES:
                self.assertIn(f"secret file {name}: {(self.secrets / name).as_posix()}", preflight)
            self.assertNotIn("rs-partflow_app_password-value", preflight)
        cases = {
            "config --format json fails": out("", rc=1, stderr="config failed\n"),
            "no secret file named": out(compose_config_doc(self.secrets, names=())),
        }
        for name, answer in cases.items():
            with self.subTest(case=name):
                self.reset()
                self.prepend("docker", rule(r"--profile ops config --format json$", answer))
                self.release()
                self.assertExit(2, "could_not_run")
                self.assertIn("config --format json' failed or names no secret file", self.result.stderr)
                self.assertEqual(self.docker(), CURRENT_IMAGES[:2])

    # RS-7
    def test_rs7_build_inputs_and_tag(self):
        cases = {
            "modified tracked file": ("git", rule(r"^status --porcelain", out(" M backend/app/main.py\n"))),
            "untracked revision": ("git", rule(r"^status --porcelain", out("?? backend/alembic/versions/0033_stray.py\n"))),
            "untracked frontend file": ("git", rule(r"^status --porcelain", out("?? frontend/src/stray.ts\n"))),
            "ignored frontend/.env.local": ("git", rule(r"^ls-files --others --ignored", out("frontend/.env.local\n"))),
            "tag absent": ("git", rule(r"^rev-parse -q --verify", out("", rc=1))),
            "tag on another commit": ("git", rule(r"^rev-parse -q --verify", out(OTHER_COMMIT + "\n"))),
        }
        for name, (tool, override) in cases.items():
            with self.subTest(case=name):
                self.reset()
                self.prepend(tool, override)
                self.release()
                self.assertExit(1, "stopped_unchanged")
                self.assertNoDocker(r"^run ", r"--profile ops run", r" build ")
                git = [" ".join(c["argv"]) for c in self.calls("git")]
                if name in ("modified tracked file", "untracked revision", "untracked frontend file"):
                    self.assertIn(GIT_STATUS, git)
                if name == "ignored frontend/.env.local":
                    self.assertIn(GIT_IGNORED, git)
        with self.subTest(case="tag grammar"):
            self.reset()
            self.release(release="v1.1")
            self.assertEqual(self.result.returncode, 1, self.result.stderr)
            self.assertIn("DEPLOYMENT §10.2", self.result.stderr)
            self.assertEqual(self.calls(), [])

    # RS-8
    def test_rs8_existing_candidate_images(self):
        label = lambda service, value: rule(LABEL_INSPECT.replace("(backend|web)", service), out(value + "\n"))  # noqa: E731
        version = lambda service, value: rule(VERSION_INSPECT.replace("(backend|web)", service), out(value + "\n"))  # noqa: E731
        from_head = [label("backend", HEAD), label("web", HEAD)]
        cases = {
            "both with another revision": ([label("backend", OTHER_COMMIT), label("web", OTHER_COMMIT)], 1, "never rebuild an existing tag"),
            "only one present": ([label("backend", HEAD)], 1, "never rebuild an existing tag"),
            "both from HEAD": ([*from_head, version("backend", TAG), version("web", TAG)], 0, None),
            # An rc image re-tagged as the final release: its baked identity is still the rc.
            "both from HEAD, re-tagged from another release": (
                [*from_head, version("backend", "v1.1.0-rc.1"), version("web", "v1.1.0-rc.1")], 1,
                "built as release(s) v1.1.0-rc.1 v1.1.0-rc.1"),
            "both from HEAD, one re-tagged": ([*from_head, version("backend", TAG), version("web", "v1.1.0-rc.1")], 1,
                                              "re-tagged image is never reused"),
            "both from HEAD, no version label": ([*from_head, version("backend", ""), version("web", "")], 1,
                                                 "built as release(s) ? ?"),
        }
        for name, (rules, code, message) in cases.items():
            with self.subTest(case=name):
                self.reset()
                self.prepend("docker", *rules)
                self.release()
                self.assertExit(code)
                self.assertNoDocker(r" build backend web$")
                if code:
                    self.assertExit(1, "stopped_unchanged")
                    self.assertIn(message, self.result.stderr)
                    self.assertNoDocker(RUN_CHECK_J, MIGRATE, r"stop backend", r"^up ")
                else:
                    self.assertIn("reused", (self.record_dir() / "build.log").read_text(encoding="utf-8"))

    # RS-8: the candidate image must report the release and commit it is released as, before any freeze.
    def test_rs8_candidate_reports_another_release(self):
        cases = {
            "another release": (dict(release="v1.1.0-rc.1"), "reports release v1.1.0-rc.1 at commit " + HEAD),
            "another commit": (dict(commit=OTHER_COMMIT), f"reports release {TAG} at commit {OTHER_COMMIT}"),
            "no commit": (dict(commit=None), f"reports release {TAG} at commit unknown"),
        }
        for name, (identity, message) in cases.items():
            with self.subTest(case=name):
                self.reset()
                self.prepend("docker", rule(REVISION, out(revision_doc("upgrade_available", 1, expected=NEW_REVISION, readiness="mismatch",
                                                                       pending=[NEW_REVISION], **identity), rc=1), env=CANDIDATE_ENV))
                self.release()
                self.assertExit(1, "stopped_unchanged")
                self.assertIn(message, self.result.stderr)
                self.assertNoDocker(r"stop backend", MIGRATE, r"^up ")
                self.assertEqual(self.env_text(), "\n".join(self.env_lines) + "\n")

    # RS-10
    def test_rs10_rehearsal_arguments(self):
        for arguments in (("--rehearsal", "--project", "partflow-production"), ("--rehearsal",), ("--project", "partflow-s3-x")):
            with self.subTest(arguments=arguments):
                self.release(*arguments)
                self.assertEqual(self.result.returncode, 2, self.result.stderr)
                self.assertEqual(self.calls(), [])

    def test_rs10_rehearsal_mode(self):
        self.prepend("git", rule(r"^status|^ls-files|^rev-parse -q", out("should not be called", rc=99)))
        self.release("--rehearsal", "--project", "partflow-s3-x")
        self.assertExit(0)
        self.assertEqual([" ".join(c["argv"]) for c in self.calls("git")], ["rev-parse HEAD"])
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            if text.startswith("compose"):
                self.assertTrue(text.startswith("compose -p partflow-s3-x -f compose.production.yaml"), text)
        record = self.record()
        self.assertIs(record["rehearsal"], True)
        self.assertEqual(record["environment"], "rehearsal")
        self.assertIs(record["release"]["git_tag_checked"], False)

    # RS-11
    def test_rs11_text_arguments(self):
        for value in ("a\x01b", 'a"b', "a\\b", "", "x" * 501, "line\nbreak"):
            with self.subTest(value=value):
                self.run_script(RELEASE_SH, "--release", TAG, "--operator", value, "--approver", "Owner",
                                "--no-backup-reason", "first", "--records-dir", self.records.as_posix())
                self.assertEqual(self.result.returncode, 2, self.result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.records.exists())

    def test_usage_errors(self):
        cases = (
            ("--release", TAG, "--operator", "a", "--approver", "b"),
            ("--release", TAG, "--operator", "a", "--approver", "b", "--no-backup-reason", "x", "--pre-release-backup", "y"),
            ("--release", TAG, "--operator", "a", "--approver", "b", "--no-backup-reason", "x", "--accept-pre-release-findings",
             "--skip-pre-reconcile", "r"),
            ("--release", "bad tag", "--operator", "a", "--approver", "b", "--no-backup-reason", "x"),
            ("--unknown",),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.run_script(RELEASE_SH, *arguments)
                self.assertEqual(self.result.returncode, 2, self.result.stderr)
                self.assertEqual(self.calls(), [])
        self.run_script(RELEASE_SH, "--help")
        self.assertEqual(self.result.returncode, 0)
        self.assertIn("Usage: deploy/production/release.sh", self.result.stdout)


class ReleaseAfterSwitch(Harness):
    def stale_health(self):
        self.prepend("curl", rule(r"^GET /api/health$", http(200, health_body(release=CURRENT))))

    # RS-12, RS-24
    def test_rs12_health_never_shows_the_release(self):
        self.stale_health()
        self.release()
        self.assertExit(3, "stopped_frozen")
        docker = self.docker()
        switched = docker.index("up -d --no-deps backend")
        self.assertIn("ps", docker[switched + 1:])
        self.assertIn("logs --tail=100 backend", docker[switched + 1:])
        self.assertEqual(docker[-1], "stop backend")
        self.assertNoDocker(r"--no-deps web$")
        self.assertEqual(len(self.calls("sleep")), 90)
        self.assertIn("== docker compose", self.result.stderr)
        record = self.record()
        self.assertIs(record["refrozen"], True)
        self.assertIsNone(record["writes_reopened_at"])
        self.assertSafeInvocations()

    def test_rs12_refreeze_fails(self):
        nothing_pending(self)
        self.stale_health()
        self.prepend("docker", rule(r" stop backend$", out("", rc=1, stderr="stop failed\n")))
        self.release()
        self.assertExit(4, "started_needs_review")
        self.assertIn("may be running and accepting writes", self.result.stderr)
        self.assertIs(self.record()["refrozen"], False)

    def test_rs12_smoke_fails_after_the_web_switch(self):
        self.prepend("curl", rule(r"^GET /api/health/live$", http(500, "{}")))
        self.release()
        self.assertExit(3, "stopped_frozen")
        docker = self.docker()
        self.assertLess(docker.index("up -d --no-deps web"), len(docker) - 1)
        self.assertEqual(docker[-1], "stop backend")
        record = self.record()
        self.assertEqual(record["smoke"], {"file": "smoke.txt", "exit_code": 1})
        self.assertTrue(record["writes_reopened_at"])
        self.assertIs(record["refrozen"], True)
        self.assertIn("writes were open for its duration", self.result.stderr)

    # RS-21
    def test_rs21_backend_still_running_after_stop(self):
        self.prepend("docker", rule(r"ps --status running -q backend$", out("cid-backend\n")))
        self.release()
        self.assertExit(3, "stopped_frozen")
        self.assertNoDocker(MIGRATE, r"^up ")

    # RS-22
    def test_rs22_migrate_refused_concurrent_run_stays_frozen(self):
        for code in ("migrate_running", "backend_connected"):
            with self.subTest(code=code):
                self.reset()
                self.prepend("docker", rule(MIGRATE, out(migrate_doc("refused", 1, error_code=code), rc=1)))
                self.release()
                self.assertExit(3, "stopped_frozen")
                self.assertNoDocker(r"^up ")
                self.assertIn(code, self.result.stderr)


class ReleaseInterrupted(Harness):
    # RS-25: a signal while migrate runs: the trap fires before migrate.json is read, so the outcome is unknown.
    def test_rs25_interrupted_during_migrate(self):
        for signal, code in (("TERM", 143), ("INT", 130)):
            with self.subTest(signal=signal):
                self.reset()
                self.prepend("docker", rule(MIGRATE, out(migrate_doc("upgraded", 0, after=NEW_REVISION, applied=[NEW_REVISION]),
                                                         signal=signal)))
                self.release()
                self.assertExit(code, "interrupted")
                self.assertNoDocker(r"^up ")
                self.assertEqual(self.docker()[-1], f"--profile ops run --rm -T migrate --pre-release-backup {BACKUP_REF}")
                record = self.record()
                self.assertEqual(record["migration"], {"result": "outcome_unknown", "file": "migrate.json", "log": "migrate.log"})
                self.assertEqual(record["alembic"], {"before": DB_REVISION, "after": None, "expected": NEW_REVISION})
                self.assertEqual(record["steps"][-1]["name"], "migrate")
                self.assertEqual(record["steps"][-1]["exit_code"], code)
                self.assertIsNone(record["writes_reopened_at"])
                self.assertIn("the migrate outcome is unknown", self.result.stderr)
                self.assertIn("python -m app.cli revision' before anything else", self.result.stderr)
                self.assertIn("writes stay FROZEN", self.result.stderr)
                self.assertIn(f"OPERATIONS_RUNBOOK §5 (exit {code})", self.result.stderr)
                self.assertEqual(self.env_text(), "\n".join(self.env_lines) + "\n")
                self.assertFalse((self.records / ".release.lock").exists())
                # The trap reports on the terminal, never into the step's redirected output.
                migrate = self.record_dir() / "migrate.json"
                self.assertEqual(migrate.read_text(encoding="utf-8"),
                                 migrate_doc("upgraded", 0, after=NEW_REVISION, applied=[NEW_REVISION]))
                self.assertIn("release: interrupted (exit", self.result.stdout)

    # RS-25: a signal during the backend switch: the env file already names the new release.
    def test_rs25_interrupted_during_the_backend_switch(self):
        self.prepend("docker", rule(r"up -d --no-deps backend$", out(signal="TERM")))
        self.release()
        self.assertExit(143, "interrupted")
        self.assertEqual(self.docker()[-1], "up -d --no-deps backend")
        record = self.record()
        self.assertEqual(record["migration"]["result"], "upgraded")
        self.assertEqual(record["alembic"]["after"], NEW_REVISION)
        self.assertEqual(record["steps"][-1]["name"], "switch_backend")
        self.assertEqual(record["steps"][-1]["exit_code"], 143)
        self.assertIsNone(record["writes_reopened_at"])
        self.assertIn(f"PARTFLOW_RELEASE={TAG}\n", self.env_text())
        self.assertIn(f"already names {TAG}", self.result.stderr)
        self.assertNotIn("release:", (self.record_dir() / "switch-backend.log").read_text(encoding="utf-8"))
        self.assertNotIn("migrate outcome is unknown", self.result.stderr)


class ReleaseFindings(Harness):
    FINDING = ("movement_status_consistency", "status_mismatch", "part_movement", 41)
    NEW_FINDING = ("movement_status_consistency", "status_mismatch", "part_movement", 42)

    def findings(self, pre, post):
        self.prepend("docker", rule(RECONCILE, out(reconcile_doc(1, pre), rc=1), out(reconcile_doc(1, post), rc=1)))

    # RS-17
    def test_rs17_accepted_findings_nothing_pending(self):
        for post, code in (([self.FINDING], 0), ([self.FINDING, self.NEW_FINDING], 1)):
            with self.subTest(post=post):
                self.reset()
                nothing_pending(self)
                self.findings([self.FINDING], post)
                self.release("--accept-pre-release-findings")
                self.assertExit(code)
                regression = (self.record_dir() / "regression.txt").read_text(encoding="utf-8")
                if code == 0:
                    self.assertEqual(regression, "PRE-EXISTING 1 findings\n")
                    self.assertEqual(self.record()["reconcile"]["regression"], {"file": "regression.txt", "exit_code": 0})
                else:
                    self.assertIn("NEW movement_status_consistency status_mismatch part_movement 42", regression)
                    self.assertNoDocker(r"^up ")
                    self.assertEqual(self.record()["outcome"], "stopped_unchanged")

    # RS-18
    def test_rs18_accepted_findings_with_a_migration(self):
        for post, code in (([self.FINDING], 0), ([self.NEW_FINDING], 3)):
            with self.subTest(post=post):
                self.reset()
                self.findings([self.FINDING], post)
                self.release("--accept-pre-release-findings")
                self.assertExit(code)
                if code == 3:
                    self.assertIn("NEW movement_status_consistency", self.result.stderr)
                    self.assertIn("regression.txt", self.result.stderr)
                    self.assertNoDocker(r"^up ")

    def path2(self):
        """Rollback-path-2 state: the database is newer than the running release, which runs on its override."""
        self.write_env_file([*self.env_lines, "PARTFLOW_ACCEPT_SCHEMA_REVISION=9999_newer"])
        self.prepend("docker", rule(REVISION, out(revision_doc("unknown_revision", 1, database="9999_newer", readiness="accepted",
                                                               accepted="9999_newer"), rc=1), env=CURRENT_ENV))

    # RS-19
    def test_rs19_path2_baseline_with_the_candidate(self):
        self.path2()
        self.prepend("docker",
                     rule(REVISION, out(revision_doc("current", 0, database="9999_newer", expected="9999_newer")), env=CANDIDATE_ENV),
                     rule(MIGRATE, out(migrate_doc("already_current", 0, before="9999_newer"))))
        self.release()
        self.assertExit(0, "completed")
        reconciles = self.docker_entries(RECONCILE)
        self.assertEqual([e["env"].get("PARTFLOW_RELEASE") for e in reconciles], [TAG, TAG])
        self.assertNoDocker(r"^stop backend$")
        self.assertIn("PARTFLOW_ACCEPT_SCHEMA_REVISION=\n", self.env_text())
        self.assertNotIn("9999_newer", self.env_text())
        self.assertEqual(self.record()["reconcile"]["baseline_image"], "candidate")

    # RS-20
    def test_rs20_path2_without_a_matching_image(self):
        self.path2()
        self.release()
        self.assertExit(1, "stopped_unchanged")
        self.assertIn("No image of this release has the database revision as its head, so the pre-release reconciliation"
                      " cannot run. Return to the release that created revision 9999_newer first (RUNBOOK §6 path 1), or"
                      " rerun with --skip-pre-reconcile REASON (recorded; every post-release finding then blocks).",
                      self.result.stderr)
        self.assertNoDocker(RECONCILE, r"stop backend", MIGRATE)

    def test_rs20_path2_skip_pre_reconcile(self):
        self.path2()
        self.prepend("docker", rule(RECONCILE, out(reconcile_doc(1, [self.FINDING]), rc=1)))
        self.release("--skip-pre-reconcile", "database newer than every image")
        self.assertExit(3, "stopped_frozen")
        self.assertEqual(len(self.docker_entries(RECONCILE)), 1)
        self.assertEqual(len(self.docker_entries(MIGRATE)), 1)
        record = self.record()
        self.assertEqual(record["reconcile"]["skip_reason"], "database newer than every image")
        self.assertIsNone(record["reconcile"]["pre"])

    def test_rs20_skip_with_accept_is_a_usage_error(self):
        self.release("--skip-pre-reconcile", "x", "--accept-pre-release-findings")
        self.assertEqual(self.result.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_unmatched_current_release_is_refused(self):
        self.prepend("docker", rule(REVISION, out(revision_doc("upgrade_available", 1, readiness="mismatch"), rc=1), env=CURRENT_ENV))
        self.release()
        self.assertExit(1, "stopped_unchanged")
        self.assertIn("does not match the database", self.result.stderr)
        self.assertNoDocker(RECONCILE, r" build ")


# ---------------------------------------------------------------------------
# smoke.sh
# ---------------------------------------------------------------------------


class Smoke(Harness):
    def smoke(self, *extra):
        return self.run_script(SMOKE_SH, "--release", TAG, *extra)

    def lines(self):
        return self.result.stdout.splitlines()

    def assertOnlyFailure(self, failing):
        self.assertEqual(self.result.returncode, 1 if failing else 0, self.result.stdout + self.result.stderr)
        verdicts = [line.split()[:2] for line in self.lines()]
        self.assertEqual([v[1] for v in verdicts], [f"S-{n}" for n in range(1, 9)])
        for verdict, case in verdicts:
            self.assertEqual(verdict, "FAIL" if case in failing else "PASS", self.result.stdout)

    def test_all_checks_pass(self):
        self.smoke()
        self.assertOnlyFailure(set())
        requests = [c["request"] for c in self.calls("curl")]
        self.assertIn("POST /api/partflow-smoke-gate [X-PartFlow-CSRF: 1] [Content-Type: application/json]", requests)
        self.assertIn(f"POST /api/partflow-smoke-gate [X-PartFlow-CSRF: 1] [Content-Type: application/json] [X-PartFlow-Release: {TAG}]", requests)
        for entry in self.calls("curl"):
            self.assertIn(f"http://127.0.0.1:{PORT}/", " ".join(entry["argv"]))

    def test_ss1_shell(self):
        for body in (shell_body("v0.9.0"), shell_body(None)):
            with self.subTest(body=body):
                self.reset()
                self.prepend("curl", rule(r"^GET /$", http(200, body, content_type="text/html", cache_control="no-cache")))
                self.smoke()
                self.assertOnlyFailure({"S-1"})
        with self.subTest(case="cacheable shell"):
            self.prepend("curl", rule(r"^GET /$", http(200, shell_body(), content_type="text/html", cache_control="max-age=60")))
            self.smoke()
            self.assertOnlyFailure({"S-1"})

    def test_ss2_spa_fallback(self):
        self.prepend("curl", rule(r"^GET /management/work-orders$", http(404, "{}", content_type="application/json")))
        self.smoke()
        self.assertOnlyFailure({"S-2"})

    def test_ss3_readiness(self):
        cases = (
            (http(503, health_body(schema="mismatch")), (), {"S-3"}),
            (http(200, health_body(release=CURRENT)), (), {"S-3"}),
            (http(200, health_body(schema="accepted")), (), {"S-3"}),
            (http(200, health_body(schema="accepted")), ("--allow-accepted-schema",), set()),
        )
        for answer, extra, failing in cases:
            with self.subTest(body=answer["body"], extra=extra):
                self.reset()
                self.prepend("curl", rule(r"^GET /api/health$", answer))
                self.smoke(*extra)
                self.assertOnlyFailure(failing)

    def test_ss4_liveness(self):
        self.prepend("curl", rule(r"^GET /api/health/live$", http(200, '{"status":"live","release":"v1.0.0"}')))
        self.smoke()
        self.assertOnlyFailure({"S-4"})

    def test_ss5_unknown_api_path(self):
        self.prepend("curl", rule(r"^GET /api/partflow-smoke-missing$", http(200, shell_body(), content_type="text/html")))
        self.smoke()
        self.assertOnlyFailure({"S-5"})

    def test_ss6_gate_refusal(self):
        for answer in (http(404, '{"detail":"Not Found"}'), http(409, '{"detail":"Confirm first.","confirmation_required":true}')):
            with self.subTest(answer=answer):
                self.reset()
                self.prepend("curl", rule(r"^POST /api/partflow-smoke-gate", answer, not_match="X-PartFlow-Release"))
                self.smoke()
                self.assertOnlyFailure({"S-6"})

    def test_ss7_gate_pass(self):
        self.prepend("curl", rule(r"^POST /api/partflow-smoke-gate .*X-PartFlow-Release", http(409, '{"release_mismatch":true}')))
        self.smoke()
        self.assertOnlyFailure({"S-7"})

    def test_ss8_running_images(self):
        for service in ("backend", "web"):
            with self.subTest(service=service):
                self.reset()
                self.prepend("docker", rule(r"^inspect --format \{\{\.Image\}\} cid-" + service + "$", out("sha256:old\n")))
                self.smoke()
                self.assertOnlyFailure({"S-8"})
        with self.subTest(case="service not running"):
            self.prepend("docker", rule(r"ps -q web$", out("")))
            self.smoke()
            self.assertOnlyFailure({"S-8"})

    def test_smoke_with_project_and_connection_refused(self):
        self.prepend("curl", rule(r".*", {"rc": 7}))
        self.smoke("--project", "partflow-s3-x")
        self.assertEqual(self.result.returncode, 1)
        self.assertEqual(len([line for line in self.lines() if line.startswith("FAIL")]), 7)
        compose = [" ".join(c["argv"]) for c in self.calls("docker") if c["argv"][0] == "compose"]
        self.assertTrue(compose and all(c.startswith("compose -p partflow-s3-x ") for c in compose), compose)

    def test_smoke_cannot_run(self):
        self.smoke("--env-file", "missing.env")
        self.assertEqual(self.result.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.run_script(SMOKE_SH)
        self.assertEqual(self.result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
