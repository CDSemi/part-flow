"""P16-S3 release rehearsal: three releases through deploy/production/release.sh on a throwaway Compose project, both
rollback paths, a write freeze with an in-flight import, migrate refusing a connected backend and the health cost
(P16-S3 SPEC section 6.4, cases RH-1..RH-10; RH-9 is the manual browser check on a kept stack).

Usage (from any directory, on a host with the docker CLI, Compose v2, git, curl and a POSIX sh):
  python deploy/production/tests/release_rehearsal.py --evidence <path.json> [--keep] [--project NAME]

Releases, built from a temporary copy of backend/, frontend/ and both production Compose files (the repository is
never modified), all with PARTFLOW_COMMIT=$(git rev-parse HEAD) so release.sh --rehearsal reuses them:
  A = the checkout; B = A + the rehearsal-only no-op revision 9999_s3_rehearsal_noop (down_revision = head);
  C = B under another tag (no migration); D = B + 9998_s3_rehearsal_second after 9999 (RH-8 only).

Isolation: only the project `partflow-s3-rehearsal` (or --project) is created: its own containers, networks (edge
172.30.252.0/24), volume, a free loopback port, a generated env file, a temporary secrets directory and records
directory. The run refuses the projects `partflow`, `partflow-production` and `partflow-staging` and any project that
already has containers or volumes, strips host PARTFLOW_*/POSTGRES_*/COMPOSE_* variables, and never touches the
development stack or the OPS lane. In `finally` it runs `down -v --remove-orphans` for that project only and removes
the rehearsal image tags partflow/{backend,web}:s3-rh-{a,b,c,d} (unless --keep, which prints the teardown commands).

Evidence JSON: per case pass / fail / manual with the observed answers (never a password, token or cookie value),
the release.sh records and outputs (exit codes, steps), probe timelines and latencies. Exit 0 = every automated case
passed; 1 = a case failed or the run broke.
"""
import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack_smoke import (  # noqa: E402  (the stack smoke's HTTP client and helpers)
    IMPORT_HEADER,
    TOKEN_PATTERN,
    Answer,
    CaseFailure,
    Client,
    check,
    display,
    free_port,
)

REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO / "compose.production.yaml"
BUILD_FILE = REPO / "compose.production.build.yaml"
ENV_EXAMPLE = REPO / ".env.production.example"
RELEASE_SH = REPO / "deploy" / "production" / "release.sh"
SMOKE_SH = REPO / "deploy" / "production" / "smoke.sh"
DEFAULT_PROJECT = "partflow-s3-rehearsal"
FORBIDDEN_PROJECTS = ("partflow", "partflow-production", "partflow-staging")
EDGE_SUBNET = "172.30.252.0/24"
RELEASES = {"A": "s3-rh-a", "B": "s3-rh-b", "C": "s3-rh-c", "D": "s3-rh-d"}
NOOP_REVISION = "9999_s3_rehearsal_noop"
SECOND_REVISION = "9998_s3_rehearsal_second"
RELEASE_HEADER = "X-PartFlow-Release"
GATE_PATH = "/api/partflow-smoke-gate"
IMPORT_WORK_ORDERS = 2000
COPY_IGNORE = shutil.ignore_patterns(
    "node_modules", "dist", "coverage", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"
)
_HOST_VARIABLE_PREFIXES = ("PARTFLOW_", "POSTGRES_", "COMPOSE_")
ROW_COUNTS_SQL = (
    "select string_agg(table_name || '=' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from"
    " public.%I', table_name), false, true, '')))[1]::text, ',' order by table_name) from information_schema.tables"
    " where table_schema = 'public' and table_type = 'BASE TABLE'"
)
NOOP_TEMPLATE = '''"""P16-S3 release rehearsal only: a no-op revision ({revision}); never committed."""

revision = "{revision}"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def alembic_head(versions):
    revisions, parents = set(), set()
    for path in versions.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        found = re.search(r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        down = re.search(r'^down_revision(?::[^=]+)?\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if found:
            revisions.add(found.group(1))
        if down:
            parents.add(down.group(1))
    heads = sorted(revisions - parents)
    if len(heads) != 1:
        raise CaseFailure(f"expected one Alembic head in {versions}, found {heads}")
    return heads[0]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def probe_verdict(samples, release_a, release_b):
    """RH-2 over the probe timeline (one sample per probe round: the POST status with header A, the shell's release).

    Expected order: 404 (backend A passes A's write to a path with no route), the freeze (web's JSON 502/504), 409
    release_mismatch once backend B runs while web still serves shell A, then shell B (the web switch) with 409 on.
    A connection-level error (no HTTP answer: the published port has no listener while `up -d --no-deps web` replaces
    the container) is accepted only inside the web switch, i.e. after the first 409 and before the first B shell; it
    is no write and no pass-through. Returns {"violations": [...], "first_409_index", "first_b_shell_index",
    "web_switch_errors"}.
    """
    violations = []
    statuses = [s.get("post") for s in samples]
    first_409 = next((i for i, s in enumerate(statuses) if s == 409), None)
    first_b_shell = next((i for i, s in enumerate(samples) if s.get("shell") == release_b), None)

    def errored(sample):
        return any(isinstance(sample.get(key), str) and sample[key].startswith("error") for key in ("post", "shell"))

    def in_web_switch(index):
        return first_409 is not None and first_b_shell is not None and first_409 < index < first_b_shell

    switch_errors = [i for i, s in enumerate(samples) if errored(s) and in_web_switch(i)]
    other_errors = [i for i, s in enumerate(samples) if errored(s) and not in_web_switch(i)]
    answered = [s for i, s in enumerate(statuses) if i not in switch_errors]
    if other_errors:
        violations.append(f"probe errors outside the web switch at samples {other_errors}")
    unexpected = sorted({str(s) for i, s in enumerate(statuses) if i not in switch_errors + other_errors} - {"404", "409", "502", "504"})
    if unexpected:
        violations.append(f"unexpected probe answers {unexpected}")
    if not (502 in answered or 504 in answered):
        violations.append("the probe never saw the freeze (backend stopped)")
    if first_409 is None:
        violations.append("the probe never saw the release gate refuse A")
    elif 404 in statuses[first_409:]:
        violations.append("a write with release A passed after backend B started")
    if first_b_shell is None:
        violations.append("web never served B")
    elif first_409 is not None and first_b_shell <= first_409:
        violations.append("web served B before backend B refused A (writes reopened early)")
    if first_b_shell is not None and not all(
        s.get("shell") == release_a for i, s in enumerate(samples[:first_b_shell]) if i not in switch_errors
    ):
        violations.append("shell before the web switch is not A")
    return {"violations": violations, "first_409_index": first_409, "first_b_shell_index": first_b_shell,
            "web_switch_errors": switch_errors}


class Prober(threading.Thread):
    """RH-2: every 0.5 s a POST with the release-A header to a path with no route, and GET / for the shell release."""

    def __init__(self, port, header_release):
        super().__init__(daemon=True)
        self.client = Client(port)
        self.header_release = header_release
        self.stop_event = threading.Event()
        self.origin = time.monotonic()
        self.samples = []

    def run(self):
        while not self.stop_event.is_set():
            sample = {"t_s": round(time.monotonic() - self.origin, 2)}
            try:
                answer = self.client.request(
                    "POST", GATE_PATH, timeout=20, with_cookie=False,
                    headers={"X-PartFlow-CSRF": "1", "Content-Type": "application/json", RELEASE_HEADER: self.header_release},
                    body=b"{}",
                )
                sample["post"] = answer.status
                if answer.status == 409:
                    sample["release_mismatch"] = answer.json().get("release_mismatch")
            except (OSError, ValueError) as exc:
                sample["post"] = f"error {type(exc).__name__}"
            try:
                shell = self.client.request("GET", "/", timeout=20, with_cookie=False)
                meta = re.search(rb'<meta name="partflow-release" content="([^"]*)">', shell.body)
                sample["shell"] = meta.group(1).decode("ascii") if meta else None
            except OSError as exc:
                sample["shell"] = f"error {type(exc).__name__}"
            self.samples.append(sample)
            self.stop_event.wait(0.5)


class Rehearsal:
    def __init__(self, args):
        self.args = args
        self.project = args.project
        self.workdir = Path(tempfile.mkdtemp(prefix="pf-s3-rehearsal-"))
        self.port = free_port()
        self.client = Client(self.port)
        self.created = False
        self.built = []
        self.evidence = {
            "slice": "P16-S3",
            "project": self.project,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "host": {"platform": platform.platform(), "python": platform.python_version()},
            "releases": dict(RELEASES),
            "commands": [],
            "cases": {},
        }

    # -- commands -------------------------------------------------------------

    def environment(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(_HOST_VARIABLE_PREFIXES)}
        env.update(extra)
        return env

    def run(self, command, timeout=600, check_rc=True, env_extra=None, record_output=False, cwd=REPO):
        started = time.monotonic()
        try:
            result = subprocess.run(
                command, cwd=cwd, env=self.environment(**(env_extra or {})), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            self.evidence["commands"].append({"command": display(command, env_extra), "rc": "timeout", "seconds": timeout})
            raise CaseFailure(f"timed out after {timeout} s: {display(command, env_extra)}") from exc
        entry = {"command": display(command, env_extra), "rc": result.returncode, "seconds": round(time.monotonic() - started, 2)}
        if record_output or result.returncode != 0:
            entry["output_tail"] = (result.stdout + result.stderr)[-2000:]
        self.evidence["commands"].append(entry)
        if check_rc and result.returncode != 0:
            raise CaseFailure(f"exit {result.returncode}: {display(command, env_extra)}\n{(result.stdout + result.stderr)[-1500:]}")
        return result

    def compose(self, *arguments, release=None, **kwargs):
        """`$PF -p <project>` with the generated env file; `release` puts a candidate tag in the shell."""
        env_extra = dict(kwargs.pop("env_extra", None) or {})
        if release is not None:
            env_extra["PARTFLOW_RELEASE"] = release
        command = ["docker", "compose", "-p", self.project, "-f", str(COMPOSE_FILE), "--env-file", str(self.env_file)]
        return self.run(command + list(arguments), env_extra=env_extra, **kwargs)

    def container(self, service):
        ids = self.compose("ps", "-q", service).stdout.split()
        check(len(ids) == 1, f"expected one {service} container, found {ids}")
        return ids[0]

    def inspect(self, container_id):
        return json.loads(self.run(["docker", "inspect", container_id]).stdout)[0]

    def set_env(self, release, accept=""):
        overrides = {
            "PARTFLOW_RELEASE": release,
            "PARTFLOW_ACCEPT_SCHEMA_REVISION": accept,
            "PARTFLOW_SECRETS_DIR": self.secrets_dir.as_posix(),
            "PARTFLOW_SITE_TIMEZONE": "UTC",
            "PARTFLOW_HTTP_PORT": str(self.port),
            "PARTFLOW_EDGE_SUBNET": EDGE_SUBNET,
        }
        lines = []
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0]
            lines.append(f"{key}={overrides[key]}" if "=" in line and key in overrides else line)
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def env_value(self, key):
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return None

    def row_counts(self):
        user = self.env_value("POSTGRES_USER")
        database = self.env_value("POSTGRES_DB")
        result = self.compose("exec", "-T", "db", "psql", "-U", user, "-d", database, "-At", "-c", ROW_COUNTS_SQL)
        return dict(item.split("=", 1) for item in result.stdout.strip().split(","))

    # -- case bookkeeping -----------------------------------------------------

    @contextlib.contextmanager
    def case(self, case_id):
        record = {"status": "fail", "observed": {}}
        self.evidence["cases"][case_id] = record
        started = time.monotonic()
        try:
            yield record["observed"]
            record["status"] = "pass"
        except CaseFailure as exc:
            record["reason"] = str(exc)
        except Exception as exc:  # A broken case is a failed case; the run continues with the next one.
            record["reason"] = f"{type(exc).__name__}: {exc}"
            record["traceback"] = traceback.format_exc()[-2000:]
        finally:
            record["seconds"] = round(time.monotonic() - started, 2)
            print(f"{case_id}: {record['status']}" + (f" ({record.get('reason', '')[:300]})" if record["status"] != "pass" else ""), flush=True)

    # -- setup ----------------------------------------------------------------

    def refuse_foreign_project(self):
        if self.project in FORBIDDEN_PROJECTS:
            raise SystemExit(f"Refusing to rehearse as project {self.project!r}.")
        label = f"label=com.docker.compose.project={self.project}"
        containers = self.run(["docker", "ps", "-a", "-q", "--filter", label]).stdout.split()
        volumes = self.run(["docker", "volume", "ls", "-q", "--filter", label]).stdout.split()
        if containers or volumes:
            raise SystemExit(
                f"Refusing to run: project {self.project!r} already has containers or volumes this run did not create."
                f" Remove them first: docker compose -p {self.project} down -v --remove-orphans"
            )

    def prepare(self):
        self.secrets_dir = self.workdir / "secrets"
        self.secrets_dir.mkdir()
        password_file = self.secrets_dir / "postgres_password"
        password_file.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
        os.chmod(password_file, 0o444)
        self.env_file = self.workdir / "rehearsal.env"
        self.records = self.workdir / "records"
        self.set_env(RELEASES["A"])
        self.head = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        # A: the checkout (working tree) as the build context; B, D add rehearsal-only revisions.
        self.sources = {}
        source_a = self.workdir / "src-a"
        source_a.mkdir()
        for name in ("backend", "frontend"):
            shutil.copytree(REPO / name, source_a / name, ignore=COPY_IGNORE)
        for path in (COMPOSE_FILE, BUILD_FILE):
            shutil.copyfile(path, source_a / path.name)
        self.code_head = alembic_head(source_a / "backend" / "alembic" / "versions")
        source_b = self.workdir / "src-b"
        shutil.copytree(source_a, source_b)
        (source_b / "backend" / "alembic" / "versions" / f"{NOOP_REVISION}.py").write_text(
            NOOP_TEMPLATE.format(revision=NOOP_REVISION, down=self.code_head), encoding="utf-8", newline="\n"
        )
        source_d = self.workdir / "src-d"
        shutil.copytree(source_b, source_d)
        (source_d / "backend" / "alembic" / "versions" / f"{SECOND_REVISION}.py").write_text(
            NOOP_TEMPLATE.format(revision=SECOND_REVISION, down=NOOP_REVISION), encoding="utf-8", newline="\n"
        )
        self.sources = {"A": source_a, "B": source_b, "C": source_b, "D": source_d}
        self.evidence.update({"port": self.port, "edge_subnet": EDGE_SUBNET, "commit": self.head, "code_head": self.code_head})

    def build(self, name):
        source = self.sources[name]
        tag = RELEASES[name]
        self.built.append(tag)
        self.run(
            ["docker", "compose", "-p", self.project, "-f", str(source / COMPOSE_FILE.name), "--env-file", str(self.env_file),
             "-f", str(source / BUILD_FILE.name), "build", "backend", "web"],
            timeout=2400, env_extra={"PARTFLOW_RELEASE": tag, "PARTFLOW_COMMIT": self.head},
        )

    def health(self, timeout=10):
        answer = self.client.request("GET", "/api/health", timeout=timeout)
        return answer, (answer.json() if answer.body.startswith(b"{") else {})

    def wait_for(self, predicate, seconds, what):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.health()
                if predicate(*last):
                    return last
            except OSError as exc:
                last = (exc, {})
            time.sleep(1)
        raise CaseFailure(f"{what} not reached within {seconds} s (last: {describe_health(last)})")

    def wait_healthy(self, service, seconds=120):
        deadline = time.monotonic() + seconds
        status = None
        while time.monotonic() < deadline:
            status = self.inspect(self.container(service))["State"].get("Health", {}).get("Status")
            if status == "healthy":
                return status
            time.sleep(2)
        raise CaseFailure(f"{service} is not healthy within {seconds} s (last {status})")

    def release(self, name, *extra):
        tag = RELEASES[name]
        result = self.run(
            ["sh", RELEASE_SH.as_posix(), "--release", tag, "--operator", "S3 rehearsal", "--approver", "S3 rehearsal",
             "--no-backup-reason", "s3 rehearsal: throwaway database", "--env-file", self.env_file.as_posix(),
             "--records-dir", self.records.as_posix(), "--rehearsal", "--project", self.project, *extra],
            timeout=1800, check_rc=False, record_output=True,
        )
        records = sorted(self.records.glob(f"*-{tag}/record.json"))
        record = json.loads(records[-1].read_text(encoding="utf-8")) if records else None
        return result, record

    def write_headers(self, release, **extra):
        return {"X-PartFlow-CSRF": "1", "Content-Type": "application/json", RELEASE_HEADER: release, **extra}

    # -- cases ----------------------------------------------------------------

    def rh1_install_a(self):
        with self.case("RH-1") as observed:
            for name in ("A", "B", "C", "D"):
                self.build(name)
            self.compose("up", "-d", "--wait", "db", timeout=300)
            migrate = self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason",
                                   "first install: empty database", timeout=300, check_rc=False)
            report = json.loads(migrate.stdout)
            observed["migrate"] = {"rc": migrate.returncode, "result": report.get("result"),
                                   "revision_after": report.get("revision_after"), "applied": len(report.get("applied_revisions") or [])}
            check(migrate.returncode == 0 and report.get("result") == "upgraded", "first-install migrate did not upgrade")
            check(report.get("revision_after") == self.code_head, "first install is not at the code head")
            self.compose("up", "-d", "backend", "web", timeout=300, env_extra={"PARTFLOW_BACKEND_WORKERS": "1"})
            answer, body = self.wait_for(lambda a, b: getattr(a, "status", None) == 200, 120, "health 200 on A")
            observed["health"] = body
            check(body.get("release") == RELEASES["A"] and body.get("schema") == "current", "health A is not current")
            check(body.get("commit") == self.head, "health A commit")
            self.first_run_setup(observed)
            self.compose("up", "-d", "backend", timeout=300)
            self.wait_for(lambda a, b: getattr(a, "status", None) == 200, 120, "health 200 on A (2 workers)")

    def first_run_setup(self, observed):
        status = self.client.request("GET", "/api/setup")
        setup = status.json()
        check(status.status == 200 and setup.get("open") is True, "setup is not open")
        tokens = TOKEN_PATTERN.findall(self.compose("logs", "--no-log-prefix", "backend").stdout)
        check(len(tokens) == 1, "expected one announced setup token")
        roles = setup["eligible_roles"]
        role = next((r for r in roles if r["name"] == "Administrator"), roles[0])
        self.admin_login = "s3-rehearsal-admin"
        self.admin_password = secrets.token_urlsafe(18)
        body = json.dumps({"setup_token": tokens[0], "login_name": self.admin_login, "display_name": "S3 Rehearsal Administrator",
                           "role_id": role["id"], "password": self.admin_password}).encode("utf-8")
        answer = self.client.request("POST", "/api/setup/administrator", headers=self.write_headers(RELEASES["A"]), body=body)
        observed["setup_status"] = answer.status
        check(answer.status == 201, f"setup answered {answer.status}: {answer.excerpt()}")
        self.client.remember_cookie(answer)
        self.user = answer.json()["user"]

    def rh2_release_b(self):
        with self.case("RH-2") as observed:
            before = self.container("backend")
            prober = Prober(self.port, RELEASES["A"])
            prober.start()
            try:
                result, record = self.release("B")
            finally:
                prober.stop_event.set()
                prober.join(timeout=60)
            samples = prober.samples
            observed.update({"rc": result.returncode, "stdout_tail": result.stdout[-1500:], "record": record, "probe": samples})
            check(result.returncode == 0 and record and record["outcome"] == "completed", f"release.sh B exit {result.returncode}")
            steps = [s["name"] for s in record["steps"]]
            check("freeze" in steps and steps.index("freeze") < steps.index("migrate"), "no freeze before migrate")
            check(record["migration"]["result"] == "upgraded", "migrate did not upgrade")
            migrate = json.loads(next(self.records.glob(f"*-{RELEASES['B']}/migrate.json")).read_text(encoding="utf-8"))
            observed["applied"] = migrate.get("applied_revisions")
            check(migrate.get("applied_revisions") == [NOOP_REVISION], "not exactly the rehearsal revision applied")
            check(record["reconcile"]["post"]["exit_code"] == 0 and record["smoke"]["exit_code"] == 0, "post reconcile or smoke")
            check(self.container("backend") != before, "backend was not recreated")
            verdict = probe_verdict(samples, RELEASES["A"], RELEASES["B"])
            observed.update({k: v for k, v in verdict.items() if k != "violations"})
            check(not verdict["violations"], "; ".join(verdict["violations"]))

    def rh3_stale_client(self):
        with self.case("RH-3") as observed:
            before = self.row_counts()
            sign_in = self.client.request(
                "POST", "/api/session", headers=self.write_headers(RELEASES["A"]), with_cookie=False,
                body=json.dumps({"login_name": self.admin_login, "password": self.admin_password}).encode("utf-8"),
            )
            grant = self.client.request(
                "PATCH", f"/api/roles/{self.user['role_id']}", headers=self.write_headers(RELEASES["A"]),
                body=json.dumps({"grant_permissions": ["MANAGE_WORK_ORDERS"]}).encode("utf-8"),
            )
            after = self.row_counts()
            observed.update({"sign_in": sign_in.summary("cache-control"), "grant": grant.summary(), "rows_before": before, "rows_after": after})
            for answer in (sign_in, grant):
                check(answer.status == 409 and answer.json().get("release_mismatch") is True, f"not 409 release_mismatch: {answer.status}")
            check(before == after, "row counts changed")

    def rh4_rollback_path2(self):
        with self.case("RH-4") as observed:
            self.set_env(RELEASES["A"])
            self.compose("up", "-d", "backend", "web", timeout=300)
            answer, body = self.wait_for(lambda a, b: b.get("release") == RELEASES["A"], 120, "health from A")
            observed["health_mismatch"] = {"status": answer.status, "body": body}
            check(answer.status == 503 and body.get("status") == "not_ready" and body.get("schema") == "mismatch", "A is not not_ready/mismatch")
            check(body.get("database_revision") == NOOP_REVISION, "database revision is not the rehearsal revision")
            refused = self.client.request("POST", "/api/session", headers=self.write_headers(RELEASES["A"]), with_cookie=False,
                                          body=json.dumps({"login_name": "nobody-s3", "password": "x"}).encode("utf-8"))
            session = self.client.request("GET", "/api/session")
            observed["write"] = refused.summary()
            observed["session_read"] = session.status
            check(refused.status == 503 and refused.json().get("not_ready") is True, "a write was not refused 503 not_ready")
            check(session.status == 200, "GET /api/session is not 200 under a mismatch")
            observed["backend_health"] = self.wait_healthy("backend")
            time.sleep(25)
            info = self.inspect(self.container("backend"))
            observed["backend_after_25s"] = {"health": info["State"]["Health"]["Status"], "restarts": info["RestartCount"]}
            check(info["State"]["Health"]["Status"] == "healthy" and info["RestartCount"] == 0, "backend flaps under a mismatch")
            self.set_env(RELEASES["A"], accept=NOOP_REVISION)
            self.compose("up", "-d", "backend", timeout=300)
            answer, body = self.wait_for(lambda a, b: getattr(a, "status", None) == 200, 120, "health 200 accepted")
            observed["health_accepted"] = body
            check(body.get("schema") == "accepted" and body.get("accepted_revision") == NOOP_REVISION, "not schema accepted")
            unknown = self.client.request("POST", "/api/session", headers=self.write_headers(RELEASES["A"]), with_cookie=False,
                                          body=json.dumps({"login_name": "nobody-s3", "password": "x"}).encode("utf-8"))
            observed["write_accepted"] = unknown.summary()
            check(unknown.status == 401, f"a write did not pass the gate on the override ({unknown.status})")

    def rh5_forward_from_path2(self):
        with self.case("RH-5") as observed:
            result, record = self.release("C")
            observed.update({"rc": result.returncode, "stdout_tail": result.stdout[-1500:], "record": record})
            check(result.returncode == 0 and record and record["outcome"] == "completed", f"release.sh C exit {result.returncode}")
            current = json.loads(next(self.records.glob(f"*-{RELEASES['C']}/current-revision.json")).read_text(encoding="utf-8"))
            observed["current_revision"] = {k: current.get(k) for k in ("state", "readiness", "database_revision")}
            check(current.get("readiness") == "accepted", "step 1 did not see the override")
            check(record["reconcile"]["baseline_image"] == "candidate", "the pre-release reconcile did not use C")
            check("freeze" not in [s["name"] for s in record["steps"]], "a freeze without a pending migration")
            check(self.env_value("PARTFLOW_ACCEPT_SCHEMA_REVISION") == "", "the override was not cleared")
            _, body = self.health()
            observed["health"] = body
            check(body.get("release") == RELEASES["C"] and body.get("schema") == "current", "health C is not current")

    def rh6_rollback_path1(self):
        with self.case("RH-6") as observed:
            self.set_env(RELEASES["B"])
            self.compose("up", "-d", "backend", "web", timeout=300)
            _, body = self.wait_for(lambda a, b: b.get("release") == RELEASES["B"] and getattr(a, "status", None) == 200, 120, "health B")
            observed["health"] = body
            check(body.get("schema") == "current", "health B is not current")
            self.wait_healthy("web", 60)
            smoke = self.run(["sh", SMOKE_SH.as_posix(), "--release", RELEASES["B"], "--env-file", self.env_file.as_posix(),
                              "--project", self.project], check_rc=False, record_output=True)
            observed["smoke"] = {"rc": smoke.returncode, "output": smoke.stdout}
            check(smoke.returncode == 0, "smoke B failed")

    def rh7_freeze_with_import(self):
        with self.case("RH-7") as observed:
            headers = self.write_headers(RELEASES["B"])
            grant = self.client.request("PATCH", f"/api/roles/{self.user['role_id']}", headers=headers,
                                        body=json.dumps({"grant_permissions": ["MANAGE_WORK_ORDERS"]}).encode("utf-8"))
            check(grant.status == 200, f"grant answered {grant.status}: {grant.excerpt()}")
            rows = "".join(f"S3RH-{i:05d},S3-RH-PN-{i:05d},10,," + "\r\n" for i in range(1, IMPORT_WORK_ORDERS + 1))
            body = (IMPORT_HEADER + rows).encode("utf-8")
            csv_headers = {**headers, "Content-Type": "text/csv"}
            preview = self.client.request("POST", "/api/work-orders/import/preview", headers=csv_headers, body=body, timeout=200)
            check(preview.status == 200, f"preview answered {preview.status}")
            report = preview.json()
            commit_headers = {**csv_headers, "X-PartFlow-Import-Check": report["check_token"]}
            result = {}

            def run_import():
                try:
                    result["answer"] = self.client.request("POST", "/api/work-orders/import", headers=commit_headers, body=body, timeout=250)
                except OSError as exc:
                    result["error"] = repr(exc)

            worker = threading.Thread(target=run_import, daemon=True)
            worker.start()
            time.sleep(3)
            stop_started = time.monotonic()
            self.compose("stop", "backend", timeout=300)
            stop_ended = time.monotonic()
            worker.join(timeout=260)
            answer = result.get("answer")
            observed["import"] = {"error": result.get("error")} if answer is None else {
                "status": answer.status, "seconds": round(answer.ended - answer.started, 2),
                "ended_after_stop_started_s": round(answer.ended - stop_started, 2),
                "summary": answer.json().get("summary") if answer.status == 200 else answer.excerpt(),
            }
            observed["stop_seconds"] = round(stop_ended - stop_started, 2)
            check(answer is not None and answer.status == 200, "the in-flight import did not complete with 200")
            check(answer.ended > stop_started, "the import was not in flight when the stop began")
            check(answer.json()["summary"]["created"] == IMPORT_WORK_ORDERS, "the import did not create every Work Order")
            seen, origin, final = [], time.monotonic(), None
            while time.monotonic() - origin < 40:
                probe = self.client.request("GET", "/api/health", timeout=20)
                data = probe.json() if probe.body.startswith(b"{") else {}
                seen.append({"t_s": round(probe.started - origin, 1), "status": probe.status, "server_unavailable": data.get("server_unavailable")})
                check(probe.status in (502, 504) and data.get("server_unavailable") is True, f"unexpected answer while frozen: {probe.status}")
                if probe.status == 502:
                    final = probe
                    break
                time.sleep(1)
            observed["while_frozen"] = seen
            check(final is not None, "no JSON 502 within 40 s of the freeze")
            self.compose("up", "-d", "backend", timeout=300)
            answer, _ = self.wait_for(lambda a, b: getattr(a, "status", None) == 200, 120, "health 200 after the reopen")
            observed["reopened"] = answer.status

    def rh8_migrate_with_backend_connected(self):
        with self.case("RH-8") as observed:
            self.health()  # leaves a pooled backend session open
            result = self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason", "rh-8",
                                  release=RELEASES["D"], check_rc=False, timeout=300)
            report = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
            revision = self.compose("run", "--rm", "--no-deps", "-T", "backend", "python", "-m", "app.cli", "revision", check_rc=False)
            state = json.loads(revision.stdout) if revision.stdout.strip().startswith("{") else {}
            observed.update({"rc": result.returncode, "result": report.get("result"), "error": report.get("error"),
                             "database_revision": state.get("database_revision")})
            check(result.returncode == 1 and (report.get("error") or {}).get("code") == "backend_connected", "migrate was not refused backend_connected")
            check(state.get("database_revision") == NOOP_REVISION, "the revision changed")

    def rh9_manual(self):
        self.evidence["cases"]["RH-9"] = {
            "status": "manual",
            "observed": {
                "note": "Browser check on the kept stack (--keep): load a page on one release, switch the server with"
                " release.sh, observe the UPDATED notice and chip, disabled writes, monitoring '● Live', the kiosk"
                " auto-reload rules and the in-modal Reload page. Recorded separately (FRONTEND).",
                "url": f"http://127.0.0.1:{self.port}/",
                "kept": self.args.keep,
            },
        }

    def rh10_health_cost(self):
        with self.case("RH-10") as observed:
            for path in ("/api/health", "/api/health/live"):
                latencies = []
                for _ in range(1000):
                    answer = self.client.request("GET", path, timeout=10, with_cookie=False)
                    check(answer.status == 200, f"{path} answered {answer.status}")
                    latencies.append((answer.ended - answer.started) * 1000)
                observed[path] = {"n": len(latencies), "p50_ms": round(statistics.median(latencies), 2),
                                  "p95_ms": round(percentile(latencies, 0.95), 2), "max_ms": round(max(latencies), 2)}

    # -- teardown -------------------------------------------------------------

    def teardown(self):
        if self.args.keep:
            self.evidence["kept"] = True
            print(
                f"Kept: http://127.0.0.1:{self.port}/  (administrator {getattr(self, 'admin_login', '-')}; the password is in"
                f" {self.workdir / 'admin-password'})\n"
                f"Tear down: docker compose -p {self.project} -f {COMPOSE_FILE} --env-file {self.env_file} down -v --remove-orphans\n"
                f"Then: docker image rm " + " ".join(f"partflow/{s}:{t}" for t in self.built for s in ("backend", "web")),
                flush=True,
            )
            if getattr(self, "admin_password", None):
                (self.workdir / "admin-password").write_text(self.admin_password + "\n", encoding="utf-8")
            return
        if self.created:
            with contextlib.suppress(CaseFailure):
                self.compose("down", "-v", "--remove-orphans", timeout=600)
        for tag in self.built:
            for service in ("backend", "web"):
                self.run(["docker", "image", "rm", f"partflow/{service}:{tag}"], check_rc=False)

    def remove_workdir(self):
        if self.args.keep:
            return
        for path in self.workdir.rglob("*"):
            if path.is_file():
                with contextlib.suppress(OSError):
                    os.chmod(path, 0o600)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def main(self):
        self.refuse_foreign_project()
        outcome = 1
        try:
            self.prepare()
            self.created = True
            self.rh1_install_a()
            if self.evidence["cases"]["RH-1"]["status"] != "pass":
                raise CaseFailure("RH-1 failed: the later cases need release A installed")
            self.rh2_release_b()
            self.rh3_stale_client()
            self.rh4_rollback_path2()
            self.rh5_forward_from_path2()
            self.rh6_rollback_path1()
            self.rh7_freeze_with_import()
            self.rh8_migrate_with_backend_connected()
            self.rh10_health_cost()
            self.rh9_manual()
            statuses = [c["status"] for c in self.evidence["cases"].values()]
            outcome = 1 if "fail" in statuses else 0
        except (CaseFailure, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            self.evidence["run_error"] = f"{type(exc).__name__}: {exc}"
            print(f"run error: {exc}", file=sys.stderr, flush=True)
        finally:
            self.teardown()
            self.remove_workdir()
            self.evidence["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            self.evidence["outcome"] = {0: "pass", 1: "fail"}[outcome]
            evidence_path = Path(self.args.evidence)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(self.evidence, indent=2, default=str) + "\n", encoding="utf-8")
            print(f"evidence: {evidence_path} ({self.evidence['outcome']})", flush=True)
        return outcome


def describe_health(last):
    if not last:
        return repr(last)
    answer, body = last
    if isinstance(answer, Answer):
        return f"HTTP {answer.status} {json.dumps(body)[:200]}"
    return repr(answer)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="P16-S3 release rehearsal (throwaway Compose project).")
    parser.add_argument("--evidence", required=True, help="path of the evidence JSON to write")
    parser.add_argument("--keep", action="store_true", help="leave the stack running for the manual RH-9 browser check")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f"Compose project name (default {DEFAULT_PROJECT})")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Rehearsal(parse_args(sys.argv[1:])).main())
