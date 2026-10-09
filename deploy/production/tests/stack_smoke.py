"""P16-S2 stack smoke: builds the production images (with compose.production.build.yaml) and runs compose.production.yaml
under its own throwaway Compose project, then checks the running stack through `web` (P16-S2 SPEC section 6.3, cases
SM-1..SM-22, plus SM-23 from the slice audit).

Usage (from any directory, on a host with the docker CLI and Compose v2):
  python deploy/production/tests/stack_smoke.py --evidence <path.json> [--keep] [--project NAME]

Isolation: only the project `partflow-s2-smoke` (or --project) is created: its own containers, networks
(`<project>_edge` on 172.30.251.0/24, `<project>_internal`), volume, a free loopback port, a generated env file and a
temporary secrets directory. The run refuses the project names `partflow` and `partflow-production` and any project
that already has containers or volumes (it never touches a stack it did not create). In `finally` it runs
`down -v --remove-orphans` for that project only, unless --keep (then the stack stays up for the manual browser check
SM-20 and the teardown command is printed). The image tags partflow/backend:s2-smoke and partflow/web:s2-smoke stay
for the layer cache.

Evidence JSON: per case id pass / fail / invalid / manual with the observed statuses, headers and body excerpts
(never a password, token or cookie value), image ids, nginx/postgres/python versions, host and Docker versions and
durations. Exit status: 0 = every automated case passed; 1 = a case failed or the run broke; 2 = no failure but a
case is invalid (host too slow) and must be re-run.

Amended by P16-S5 (P16-S5 SPEC section 4.8.1): compose.production.yaml needs PARTFLOW_BACKUP_DIR for every command, so
the generated env files name a temporary backup directory (mode 0700, inside the work directory, removed with it);
migrate runs with --no-backup-reason and reads no backup.

Spec amendments applied here (recorded in the P16-S2 evidence):
- SM-17a/SM-17b: "no output line contains ://" cannot hold (pydantic prints an errors.pydantic.dev link and uvicorn
  prints its http:// listen address); the check is "no output line carries a credential URL" (ST-10's pattern).
- SM-14: web resolves `backend` with `resolver ... valid=10s`, so for up to ~10 s (+ the 5 s connect timeout) after
  `stop backend` nginx still connects to the stopped container's cached address and answers its own JSON 504
  `server_unavailable`; once the cache expires the name no longer resolves and the answer is the JSON 502. The case
  accepts 504 then 502 (both are unknown outcomes for a write) and requires 502 within 40 s.
- SM-10/SM-11 validity guard: nginx refuses request 7 only while less than one request has leaked from the bucket
  since request 1 (rate 10r/m = one per 6 s, burst 5), so the case is `invalid (host too slow)` when request 7 started
  >= 6 s after request 1 started (the spec's "after request 6" bound is weaker and could report a false failure).

Added by the slice audit (F1):
- SM-23: before anything is built, `up -d --no-deps backend web` and `run --rm --no-deps backend` with a
  PARTFLOW_RELEASE whose images do not exist fail with "No such image" and leave no image under that tag
  (compose.production.yaml has no build section, so a missing release is never built from the checkout).

Amended by P16-S3 (P16-S3 SPEC section 6.4): the build passes PARTFLOW_COMMIT (git rev-parse HEAD) in the shell; the
setup migrates with `--profile ops run --rm -T migrate --no-backup-reason "s2 smoke"`; every unsafe request carries
`X-PartFlow-Release: s2-smoke` (the backend refuses a write from another release); new cases:
- SM-24: POST /api/partflow-smoke-gate without the release header -> 409 release_mismatch; with it -> 404.
- SM-25: /api/health reports release s2-smoke, the built commit and schema current; /api/health/live answers 200.
- SM-26: the backend container probes /api/health/live and carries RELEASE_TAG=s2-smoke; both images carry the
  version/revision labels.
- SM-27: migrate without a backup option is a usage error (exit 2); a second run is already_current (exit 0).
- SM-28: GET / carries <meta name="partflow-release" content="s2-smoke">.

Amended by P16-S4 (P16-S4 SPEC section 6.5; run it with --project partflow-s4-smoke): the secrets directory holds
postgres_password, partflow_app_password and partflow_maintenance_password; the setup provisions the database roles
(`--profile ops run --rm -T db-roles`) before its migrate, and backend connects as partflow_app. SM-17a/b use an empty
partflow_app_password (backend no longer mounts postgres_password); new cases:
- SM-17c: an empty postgres_password stops migrate and db-roles with configuration_invalid (exit 2, one JSON document).
- SM-29: db-roles twice (created, then unchanged). SM-30: migrate applied the grants (29 tables).
- SM-31: every partflow-api session in pg_stat_activity is partflow_app.
- SM-32: the privilege probe (DEPLOYMENT §3.1, one `sh -c` psql per table and statement) over the guarded tables.
- SM-33: reconcile through backend: check (h) pass, connected as partflow_app (not a superuser).
- SM-34: restore convergence: a --no-privileges dump restored into partflow_restore_s4, apply-grants, then (h) pass.
- SM-35: the write flows through web (SM-6, SM-7..SM-9, SM-21, SM-36) pass as partflow_app.
- SM-36: application-password rotation in a short write freeze (stop backend, db-roles, force-recreate backend).
SM-17c gives migrate a backup option so that its configuration is read (without one, migrate is an argparse usage
error before any configuration is read, SM-27).
"""
import argparse
import contextlib
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO / "compose.production.yaml"
BUILD_FILE = REPO / "compose.production.build.yaml"
ENV_EXAMPLE = REPO / ".env.production.example"
DEFAULT_PROJECT = "partflow-s2-smoke"
FORBIDDEN_PROJECTS = ("partflow", "partflow-production")
RELEASE = "s2-smoke"
EDGE_SUBNET = "172.30.251.0/24"
BACKEND_IMAGE = f"partflow/backend:{RELEASE}"
WEB_IMAGE = f"partflow/web:{RELEASE}"
RELEASE_HEADER = "X-PartFlow-Release"
# Every unsafe request of the smoke carries the release of the build (P16-S3 release gate) next to the CSRF header.
CSRF = {"X-PartFlow-CSRF": "1", RELEASE_HEADER: RELEASE}
LIVENESS_PATH = "/api/health/live"
RELEASE_META = f'<meta name="partflow-release" content="{RELEASE}">'
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
CREDENTIAL_URL = re.compile(r"://[^/\s]*:[^/\s]*@")
IMAGE_TOO_LARGE_MESSAGE = "The image is larger than 2 MB. Choose a smaller image."
FILE_TOO_LARGE_MESSAGE = "The file is larger than 1 MB. Split it into smaller files."
# P16-S4: backend reads only the application database role's password file.
EMPTY_PASSWORD_FILE_MESSAGE = "DATABASE_PASSWORD_FILE /run/secrets/partflow_app_password is empty."
APP_ROLE = "partflow_app"
ROLE_SECRETS = ("partflow_app_password", "partflow_maintenance_password")
# SM-32: the guarded tables (P16-S4 SPEC section 3.2) and the two cells the application database role may run.
GUARDED_TABLES = (
    "part_movements", "audit_events", "machine_lifecycle_events", "quantity_flow_lineage", "work_order_allocations",
    "worker_sessions", "assigned_route_steps",
)
PROBE_STATEMENTS = {
    "UPDATE": "UPDATE {table} SET id = id WHERE false",
    "DELETE": "DELETE FROM {table} WHERE false",
    "TRUNCATE": "TRUNCATE {table}",
}
PROBE_ALLOWED = {("worker_sessions", "UPDATE"): "UPDATE 0", ("assigned_route_steps", "DELETE"): "DELETE 0"}
RESTORE_DATABASE = "partflow_restore_s4"
CLASSIFIED_TABLES = 29
MULTI_WORKER_STOP = "failed to start, stopping the parent process"
TOKEN_PATTERN = re.compile(r"Setup token: ([A-Z2-7]{4}(?:-[A-Z2-7]{4})+)")
IMPORT_HEADER = "Work Order Number,Part Number,Requested Quantity,Job Number,Due Date\r\n"
IMPORT_WORK_ORDERS = 2000
MIB = 1024 * 1024
_HOST_VARIABLE_PREFIXES = ("PARTFLOW_", "POSTGRES_", "COMPOSE_")


class CaseFailure(AssertionError):
    pass


class CaseInvalid(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CaseFailure(message)


# ---------------------------------------------------------------------------
# HTTP through web
# ---------------------------------------------------------------------------


class Answer:
    def __init__(self, status, headers, body, started, ended):
        self.status, self.headers, self.body, self.started, self.ended = status, headers, body, started, ended

    def header(self, name):
        values = [v for k, v in self.headers if k.lower() == name.lower()]
        return values[0] if values else None

    def header_names(self):
        return [k.lower() for k, _ in self.headers]

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def excerpt(self, limit=300):
        text = self.body[:limit].decode("utf-8", "replace")
        return text + ("..." if len(self.body) > limit else "")

    def summary(self, *headers):
        observed = {"status": self.status, "body": self.excerpt()}
        for name in headers:
            observed[name] = self.header(name)
        return observed


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = None

    def request(self, method, path, headers=None, body=None, timeout=60, with_cookie=True):
        sent = dict(headers or {})
        if with_cookie and self.cookie:
            sent["Cookie"] = self.cookie
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        started = time.monotonic()
        try:
            connection.putrequest(method, path, skip_accept_encoding=True)
            if body is not None:
                sent["Content-Length"] = str(len(body))
            for name, value in sent.items():
                connection.putheader(name, value)
            connection.endheaders()
            if body:
                # nginx may answer 413 and stop reading early: a write error here still leaves its answer to read.
                with contextlib.suppress(OSError):
                    for offset in range(0, len(body), 64 * 1024):
                        connection.send(body[offset : offset + 64 * 1024])
            response = connection.getresponse()
            data = response.read()
            return Answer(response.status, response.getheaders(), data, started, time.monotonic())
        finally:
            connection.close()

    def remember_cookie(self, answer):
        for name, value in answer.headers:
            if name.lower() == "set-cookie" and value.startswith("partflow_session="):
                self.cookie = value.split(";", 1)[0]


# ---------------------------------------------------------------------------
# The smoke run
# ---------------------------------------------------------------------------


class Smoke:
    def __init__(self, args):
        self.args = args
        self.project = args.project
        self.evidence = {
            "slice": "P16-S2 (amended by P16-S3 and P16-S4)",
            "project": self.project,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "host": {"platform": platform.platform(), "python": platform.python_version()},
            "commands": [],
            "cases": {},
            "spec_amendments": [
                "SM-17a/b: 'no output line carries a credential URL' (://user:password@) replaces 'no line contains ://'",
                "SM-14: a web JSON 504 server_unavailable is accepted while the resolver cache (valid=10s) holds the"
                " stopped backend's address; JSON 502 is required within 40 s",
                "SM-10/SM-11: invalid when request 7 started >= 6 s after request 1 (spec: after request 6)",
                "SM-17c (P16-S4): migrate runs with --no-backup-reason so that its configuration is read (without a"
                " backup option it is an argparse usage error before any configuration is read, SM-27)",
                "SM-35 (P16-S4): judged from the write-flow cases SM-6, SM-7..SM-9, SM-21 and SM-36 plus SM-31"
                " (every partflow-api session is partflow_app)",
            ],
        }
        self.workdir = Path(tempfile.mkdtemp(prefix="pf-s2-smoke-"))
        self.port = free_port()
        self.client = Client(self.port)
        self.created = False
        self.user = None

    # -- commands -------------------------------------------------------------

    def environment(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(_HOST_VARIABLE_PREFIXES)}
        env.update(extra)
        return env

    def run(self, command, timeout=600, check_rc=True, env_extra=None, record_output=False, input_text=None):
        """`input_text` goes to stdin (a password never enters the command line or the evidence)."""
        started = time.monotonic()
        try:
            result = subprocess.run(
                command, cwd=REPO, env=self.environment(**(env_extra or {})), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout, input=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            self.evidence["commands"].append({"command": display(command, env_extra), "rc": "timeout", "seconds": timeout})
            raise CaseFailure(f"timed out after {timeout} s: {display(command, env_extra)}") from exc
        entry = {"command": display(command, env_extra), "rc": result.returncode, "seconds": round(time.monotonic() - started, 2)}
        if record_output or result.returncode != 0:
            entry["output_tail"] = (result.stdout + result.stderr)[-1500:]
        self.evidence["commands"].append(entry)
        if check_rc and result.returncode != 0:
            raise CaseFailure(f"exit {result.returncode}: {display(command, env_extra)}\n{(result.stdout + result.stderr)[-1500:]}")
        return result

    def compose(self, *arguments, env_file=None, **kwargs):
        command = ["docker", "compose", "-p", self.project, "-f", str(COMPOSE_FILE), "--env-file", str(env_file or self.env_file)]
        return self.run(command + list(arguments), **kwargs)

    def container(self, service):
        result = self.compose("ps", "-q", service)
        ids = result.stdout.split()
        check(len(ids) == 1, f"expected one {service} container, found {ids}")
        return ids[0]

    def inspect(self, container_id):
        return json.loads(self.run(["docker", "inspect", container_id]).stdout)[0]

    # -- setup ----------------------------------------------------------------

    def refuse_foreign_project(self):
        if self.project in FORBIDDEN_PROJECTS:
            raise SystemExit(f"Refusing to run the smoke as project {self.project!r}.")
        label = f"label=com.docker.compose.project={self.project}"
        containers = self.run(["docker", "ps", "-a", "-q", "--filter", label]).stdout.split()
        volumes = self.run(["docker", "volume", "ls", "-q", "--filter", label]).stdout.split()
        if containers or volumes:
            raise SystemExit(
                f"Refusing to run: project {self.project!r} already has containers or volumes this run did not create."
                f" Remove them first: docker compose -p {self.project} down -v --remove-orphans"
            )

    def write_env(self, path, secrets_dir, release=RELEASE):
        overrides = {
            "PARTFLOW_RELEASE": release,
            "PARTFLOW_SECRETS_DIR": secrets_dir.as_posix(),
            "PARTFLOW_BACKUP_DIR": posix_host_path(self.backup_dir),
            "PARTFLOW_SITE_TIMEZONE": "UTC",
            "PARTFLOW_HTTP_PORT": str(self.port),
            "PARTFLOW_EDGE_SUBNET": EDGE_SUBNET,
        }
        lines = []
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0]
            lines.append(f"{key}={overrides[key]}" if "=" in line and key in overrides else line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_secrets(self, directory, empty=()):
        """The three secret files (P16-S4), each a distinct 32-character value; the names in `empty` are empty."""
        directory.mkdir()
        for name in ("postgres_password", *ROLE_SECRETS):
            path = directory / name
            path.write_text("" if name in empty else secrets.token_urlsafe(24) + "\n", encoding="utf-8")
            os.chmod(path, 0o444)

    def replace_secret(self, name, value):
        path = self.secrets_dir / name
        os.chmod(path, 0o600)
        path.write_text(value + "\n", encoding="utf-8")
        os.chmod(path, 0o444)

    def read_secret(self, name):
        return (self.secrets_dir / name).read_text(encoding="utf-8").strip()

    def prepare(self):
        self.backup_dir = make_backup_dir(self.workdir)
        self.secrets_dir = self.workdir / "secrets"
        self.write_secrets(self.secrets_dir)
        self.env_file = self.workdir / "smoke.env"
        self.write_env(self.env_file, self.secrets_dir)
        # SM-17a/b: a secrets directory whose partflow_app_password (the backend's only secret) is empty.
        self.empty_secrets_dir = self.workdir / "secrets-empty"
        self.write_secrets(self.empty_secrets_dir, empty=("partflow_app_password",))
        self.empty_env_file = self.workdir / "smoke-empty-secret.env"
        self.write_env(self.empty_env_file, self.empty_secrets_dir)
        # SM-17c: a secrets directory whose postgres_password (migrate, db-roles) is empty.
        self.empty_owner_secrets_dir = self.workdir / "secrets-empty-owner"
        self.write_secrets(self.empty_owner_secrets_dir, empty=("postgres_password",))
        self.empty_owner_env_file = self.workdir / "smoke-empty-owner-secret.env"
        self.write_env(self.empty_owner_env_file, self.empty_owner_secrets_dir)
        # SM-23: a release whose images were never built.
        self.missing_release = f"s2-smoke-missing-{secrets.token_hex(4)}"
        self.missing_env_file = self.workdir / "smoke-missing-release.env"
        self.write_env(self.missing_env_file, self.secrets_dir, release=self.missing_release)
        self.evidence["port"] = self.port
        self.evidence["edge_subnet"] = EDGE_SUBNET

    def start(self):
        self.created = True
        self.sm23_missing_release()
        self.head = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.compose("-f", str(BUILD_FILE), "build", timeout=1800, env_extra={"PARTFLOW_COMMIT": self.head})
        self.compose("up", "-d", "--wait", "db", timeout=300)
        # P16-S4: the database roles exist before the first migrate (migrate refuses roles_not_provisioned otherwise).
        self.first_db_roles = self.compose("--profile", "ops", "run", "--rm", "-T", "db-roles", timeout=300, record_output=True)
        self.first_migrate = self.compose(
            "--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason", "s2 smoke", timeout=300, record_output=True
        )
        # First-run setup procedure: one worker, set in the shell (overrides the env file).
        self.compose("up", "-d", "backend", "web", timeout=300, env_extra={"PARTFLOW_BACKEND_WORKERS": "1"})
        self.wait_for_health(90)

    def image_exists(self, image):
        return self.run(["docker", "image", "inspect", image], check_rc=False).returncode == 0

    def sm23_missing_release(self):
        with self.case("SM-23") as observed:
            images = [f"partflow/{name}:{self.missing_release}" for name in ("backend", "web")]
            if any(self.image_exists(image) for image in images):
                raise CaseInvalid(f"an image tagged {self.missing_release} already exists")
            attempts = {
                "up": ("up", "-d", "--no-deps", "backend", "web"),
                "run": ("run", "--rm", "--no-deps", "-T", "backend", "python", "--version"),
            }
            for label, arguments in attempts.items():
                result = self.compose(*arguments, env_file=self.missing_env_file, check_rc=False, timeout=300)
                output = result.stdout + result.stderr
                observed[label] = {"rc": result.returncode, "no_such_image": "No such image" in output}
                check(result.returncode != 0, f"`{label}` with a missing release did not fail")
                check("No such image" in output, f"`{label}` with a missing release did not fail on the missing image")
            left = [image for image in images if self.image_exists(image)]
            observed["images_created"] = left
            if left:
                # A defective run would also have started containers; the teardown removes those with the project.
                self.compose("down", "--remove-orphans", env_file=self.missing_env_file, check_rc=False, timeout=300)
                self.run(["docker", "image", "rm", *left], check_rc=False)
            check(not left, f"images were created under the missing release: {left}")

    def wait_for_health(self, seconds):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.client.request("GET", "/api/health", timeout=5)
                if last.status == 200:
                    return last
            except OSError as exc:
                last = exc
            time.sleep(1)
        raise CaseFailure(f"/api/health did not answer 200 within {seconds} s (last: {describe(last)})")

    def record_versions(self):
        versions = {}
        for image in (BACKEND_IMAGE, WEB_IMAGE, "postgres:16.14"):
            versions[image] = self.run(["docker", "image", "inspect", "--format", "{{.Id}}", image]).stdout.strip()
        self.evidence["images"] = versions
        nginx = self.compose("exec", "-T", "web", "nginx", "-v")
        self.evidence["nginx_version"] = (nginx.stdout + nginx.stderr).strip()
        self.evidence["postgres_version"] = self.compose("exec", "-T", "db", "postgres", "--version").stdout.strip()
        self.evidence["python_version"] = self.compose("exec", "-T", "backend", "python", "--version").stdout.strip()
        docker = self.run(["docker", "version", "--format", "{{json .}}"])
        info = json.loads(docker.stdout)
        self.evidence["docker"] = {
            "client": info.get("Client", {}).get("Version"),
            "server": (info.get("Server") or {}).get("Version"),
            "server_os": (info.get("Server") or {}).get("Os"),
            "kernel": (info.get("Server") or {}).get("KernelVersion"),
        }
        self.evidence["compose_version"] = self.run(["docker", "compose", "version", "--short"]).stdout.strip()

    # -- case bookkeeping -----------------------------------------------------

    @contextlib.contextmanager
    def case(self, case_id):
        record = {"status": "fail", "observed": {}}
        self.evidence["cases"][case_id] = record
        started = time.monotonic()
        try:
            yield record["observed"]
            record["status"] = "pass"
        except CaseInvalid as exc:
            record["status"] = "invalid"
            record["reason"] = str(exc)
        except CaseFailure as exc:
            record["reason"] = str(exc)
        except Exception as exc:  # A broken case is a failed case; the run continues with the next one.
            record["reason"] = f"{type(exc).__name__}: {exc}"
            record["traceback"] = traceback.format_exc()[-2000:]
        finally:
            record["seconds"] = round(time.monotonic() - started, 2)
            print(f"{case_id}: {record['status']}" + (f" ({record.get('reason', '')[:300]})" if record["status"] != "pass" else ""), flush=True)

    # -- cases ----------------------------------------------------------------

    def sm1_to_sm5(self):
        shell = None
        with self.case("SM-1") as observed:
            answer = self.client.request("GET", "/")
            shell = answer
            observed.update(answer.summary("content-type", "cache-control", "x-content-type-options", "content-security-policy", "server", "referrer-policy"))
            check(answer.status == 200, "GET / is not 200")
            check((answer.header("content-type") or "").startswith("text/html"), "not text/html")
            check(answer.header("cache-control") == "no-cache", "Cache-Control is not no-cache")
            check(answer.header("x-content-type-options") == "nosniff", "no nosniff")
            check(answer.header("content-security-policy") == CSP, "CSP differs from the spec")
            check(not [n for n in answer.header_names() if n.startswith("access-control-")], "Access-Control-* present")
            check(answer.header("server") == "nginx", "Server header carries a version")
        with self.case("SM-2") as observed:
            answer = self.client.request("GET", "/management/work-orders")
            observed.update(answer.summary("content-type", "cache-control"))
            check(answer.status == 200, "deep route is not 200")
            check(shell is not None and answer.body == shell.body, "deep route is not the index.html shell")
        with self.case("SM-3") as observed:
            check(shell is not None, "no shell")
            assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', shell.body.decode("utf-8"))
            observed["asset"] = assets[0] if assets else None
            check(assets, "index.html references no hashed asset")
            answer = self.client.request("GET", assets[0])
            observed.update({"status": answer.status, "cache-control": answer.header("cache-control")})
            check(answer.status == 200, "asset is not 200")
            check("immutable" in (answer.header("cache-control") or ""), "asset is not immutable")
        with self.case("SM-4") as observed:
            answer = self.client.request("GET", "/assets/partflow-missing-chunk.js")
            observed.update(answer.summary("content-type"))
            check(answer.status == 404, "missing chunk is not 404")
            check(b'<div id="root"' not in answer.body and (shell is None or answer.body != shell.body), "missing chunk got the app shell")
        with self.case("SM-5") as observed:
            health = self.client.request("GET", "/api/health")
            missing = self.client.request("GET", "/api/no-such-route")
            observed["health"] = health.summary("content-type")
            observed["no_such_route"] = missing.summary("content-type")
            check(health.status == 200 and health.json().get("status") == "ok", "health is not 200 ok")
            check(missing.status == 404 and missing.json() == {"detail": "Not Found"}, "unknown /api route is not the backend JSON 404")

    def sm24_to_sm28(self):
        with self.case("SM-24") as observed:
            gate = {"X-PartFlow-CSRF": "1", "Content-Type": "application/json"}
            refused = self.client.request("POST", "/api/partflow-smoke-gate", headers=gate, body=b"{}")
            passed = self.client.request("POST", "/api/partflow-smoke-gate", headers={**gate, RELEASE_HEADER: RELEASE}, body=b"{}")
            observed["without_header"] = refused.summary("content-type", "cache-control")
            observed["with_header"] = passed.summary("content-type")
            check(refused.status == 409 and refused.json().get("release_mismatch") is True, "no 409 release_mismatch without the header")
            check(refused.header("cache-control") == "no-store", "the 409 is cacheable")
            check(passed.status == 404, "the gate did not pass with the release header")
        with self.case("SM-25") as observed:
            health = self.client.request("GET", "/api/health")
            live = self.client.request("GET", LIVENESS_PATH)
            observed["health"] = health.summary()
            observed["live"] = live.summary()
            body = health.json()
            check(health.status == 200 and body.get("release") == RELEASE, "/api/health does not report the release")
            check(re.fullmatch(r"[0-9a-f]{40}", str(body.get("commit"))) is not None, "/api/health has no 40-hex commit")
            check(body.get("commit") == self.head, "/api/health commit is not the built commit")
            check(body.get("schema") == "current", "schema is not current")
            check(body.get("expected_revision") == body.get("database_revision"), "the revisions differ")
            live_body = live.json() if live.status == 200 else {}
            check(live_body.get("status") == "live" and live_body.get("release") == RELEASE, "/api/health/live answer")
        with self.case("SM-26") as observed:
            info = self.inspect(self.container("backend"))
            test = " ".join(info["Config"]["Healthcheck"]["Test"])
            environment = info["Config"]["Env"]
            labels = {}
            for image in (BACKEND_IMAGE, WEB_IMAGE):
                inspected = json.loads(self.run(["docker", "image", "inspect", image]).stdout)[0]
                found = inspected["Config"].get("Labels") or {}
                labels[image] = {k: v for k, v in found.items() if k.startswith("org.opencontainers.image.")}
            observed.update({"healthcheck": test, "release_env": [e for e in environment if e.startswith("RELEASE_")], "labels": labels})
            check(LIVENESS_PATH in test, "the backend health check does not probe liveness")
            check(f"RELEASE_TAG={RELEASE}" in environment, "the backend image does not carry RELEASE_TAG")
            for image, found in labels.items():
                check(found.get("org.opencontainers.image.version") == RELEASE, f"{image} version label")
                check(found.get("org.opencontainers.image.revision") == self.head, f"{image} revision label")
        with self.case("SM-27") as observed:
            usage = self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", check_rc=False, timeout=300)
            again = self.compose(
                "--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason", "s2 smoke again", check_rc=False, timeout=300
            )
            report = json.loads(again.stdout) if again.stdout.strip().startswith("{") else {}
            observed.update({
                "usage_rc": usage.returncode, "usage_stdout": usage.stdout[:200], "again_rc": again.returncode,
                "again_result": report.get("result"), "again_applied": report.get("applied_revisions"),
            })
            check(usage.returncode == 2 and not usage.stdout.strip(), "migrate without a backup option is not a usage error")
            check(again.returncode == 0 and report.get("result") == "already_current", "the second migrate is not already_current")
        with self.case("SM-28") as observed:
            shell = self.client.request("GET", "/")
            observed["meta_present"] = RELEASE_META.encode("utf-8") in shell.body
            check(shell.status == 200 and RELEASE_META.encode("utf-8") in shell.body, "the shell does not carry its release meta")

    def sm12_sm13_sm15(self):
        with self.case("SM-12") as observed:
            dump = self.compose("exec", "-T", "web", "nginx", "-T")
            entries = re.findall(r"^\s*set_real_ip_from\s+(\S+);", dump.stdout, re.MULTILINE)
            network = json.loads(self.run(["docker", "network", "inspect", f"{self.project}_edge"]).stdout)[0]
            gateway = network["IPAM"]["Config"][0].get("Gateway")
            if not gateway:
                # With only a subnet configured, Docker reports the assigned gateway on each endpoint.
                endpoint = self.inspect(self.container("web"))["NetworkSettings"]["Networks"][f"{self.project}_edge"]
                gateway = endpoint.get("Gateway")
            observed.update({"set_real_ip_from": entries, "edge_gateway": gateway})
            check(len(entries) == 1, "not exactly one set_real_ip_from")
            check(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", entries[0]) is not None, "set_real_ip_from is not one IPv4 address")
            check(entries[0] == gateway, "set_real_ip_from is not the edge gateway")
        with self.case("SM-13") as observed:
            answer = self.client.request(
                "OPTIONS", "/api/session",
                headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-partflow-csrf"},
            )
            observed.update({"status": answer.status, "headers": sorted(set(answer.header_names()))})
            check(not [n for n in answer.header_names() if n.startswith("access-control-allow")], "CORS allow header present")
        with self.case("SM-15") as observed:
            result = self.compose("port", "web", "80")
            published = result.stdout.split()
            observed["published"] = published
            check(published == [f"127.0.0.1:{self.port}"], "web is not published on loopback only")

    def sm18(self, workers):
        case_id = "SM-18"
        with self.case(f"{case_id} ({workers} worker{'s' if workers > 1 else ''})") as observed:
            container = self.container("backend")
            info = self.inspect(container)
            top = self.run(["docker", "top", container, "-o", "pid,ppid,args"]).stdout.splitlines()
            processes = []
            for line in top[1:]:
                parts = line.split(None, 2)
                if len(parts) == 3:
                    processes.append({"pid": parts[0], "ppid": parts[1], "args": parts[2]})
            observed.update({"user": info["Config"]["User"], "processes": processes})
            check(info["Config"]["User"] == "10001:10001", "backend does not run as 10001:10001")
            check(not [p for p in processes if "--reload" in p["args"]], "a process runs with --reload")
            relevant = [p for p in processes if "multiprocessing.resource_tracker" not in p["args"]]
            servers = [p for p in relevant if "uvicorn app.main:app" in p["args"]]
            check(len(servers) == 1, "not exactly one uvicorn app.main:app process")
            if workers == 1:
                check(len(relevant) == 1, "one worker must be exactly one process")
            else:
                children = [p for p in relevant if "multiprocessing.spawn" in p["args"] and "--multiprocessing-fork" in p["args"]]
                check(len(children) == workers, f"expected {workers} spawn workers, found {len(children)}")
                check(all(p["ppid"] == servers[0]["pid"] for p in children), "a worker is not a child of the supervisor")
                check(len(relevant) == 1 + workers, "unexpected extra process")

    def sm6_first_run(self):
        with self.case("SM-6") as observed:
            # The setup screen's read first: it also announces the token if startup could not.
            status = self.client.request("GET", "/api/setup")
            setup = status.json()
            observed["setup"] = {"status": status.status, "open": setup.get("open"), "roles": setup.get("eligible_roles")}
            check(status.status == 200 and setup.get("open") is True, "setup is not open")
            logs = self.compose("logs", "--no-log-prefix", "backend").stdout
            tokens = TOKEN_PATTERN.findall(logs)
            observed["tokens_announced"] = len(tokens)
            check(len(tokens) == 1, "expected exactly one announced setup token (one worker)")
            roles = setup["eligible_roles"]
            role = next((r for r in roles if r["name"] == "Administrator"), roles[0])
            self.admin_password = secrets.token_urlsafe(18)
            body = json.dumps({
                "setup_token": tokens[0], "login_name": "s2-smoke-admin", "display_name": "S2 Smoke Administrator",
                "role_id": role["id"], "password": self.admin_password,
            }).encode("utf-8")
            answer = self.client.request("POST", "/api/setup/administrator", headers={**CSRF, "Content-Type": "application/json"}, body=body)
            cookie = answer.header("set-cookie") or ""
            attributes = [part.strip().split("=", 1)[0].lower() for part in cookie.split(";")[1:]]
            same_site = re.search(r";\s*samesite=([^;]+)", cookie, re.IGNORECASE)
            observed["create"] = {"status": answer.status, "cookie_attributes": attributes, "samesite": same_site.group(1) if same_site else None}
            check(answer.status == 201, f"setup answered {answer.status}: {answer.excerpt()}")
            check("secure" in attributes and "httponly" in attributes, "cookie lacks Secure or HttpOnly")
            check(same_site is not None and same_site.group(1).strip().lower() == "strict", "cookie is not SameSite=Strict")
            self.client.remember_cookie(answer)
            self.user = answer.json()["user"]

    def sm7_sm8_sm9(self):
        user_id = self.user["id"] if self.user else 0
        signed_in = {**CSRF}
        with self.case("SM-7") as observed:
            check(self.user is not None, "not signed in (SM-6 failed)")
            answer = self.client.request("PUT", f"/api/users/{user_id}/avatar", headers={**signed_in, "Content-Type": "image/png"}, body=b"\0" * (3 * MIB))
            observed.update(answer.summary("content-type"))
            check(answer.status == 413, "not 413")
            data = answer.json()
            check(data.get("detail") == IMAGE_TOO_LARGE_MESSAGE and "request_too_large" not in data, "not the backend's image 413")
        with self.case("SM-8") as observed:
            check(self.user is not None, "not signed in (SM-6 failed)")
            answer = self.client.request("POST", "/api/work-orders/import/preview", headers={**signed_in, "Content-Type": "text/csv"}, body=b"a" * (2 * MIB))
            observed.update(answer.summary("content-type"))
            check(answer.status == 413, "not 413")
            data = answer.json()
            check(data.get("detail") == FILE_TOO_LARGE_MESSAGE and "request_too_large" not in data, "not the backend's file 413")
        with self.case("SM-9") as observed:
            check(self.user is not None, "not signed in (SM-6 failed)")
            theme = self.client.request("PUT", "/api/session/theme-preference", headers={**signed_in, "Content-Type": "application/json"}, body=b" " * (2 * MIB))
            avatar = self.client.request("PUT", f"/api/users/{user_id}/avatar", headers={**signed_in, "Content-Type": "image/png"}, body=b"\0" * (4 * MIB + MIB // 2))
            observed["theme_2mib"] = theme.summary("content-type", "cache-control")
            observed["avatar_4_5mib"] = avatar.summary("content-type", "cache-control")
            for answer in (theme, avatar):
                check(answer.status == 413, "not 413")
                check(answer.json().get("request_too_large") is True, "not web's 413 JSON")
                check(answer.header("cache-control") == "no-store", "web's 413 is cacheable")

    def burst(self, forwarded, count=7):
        answers = []
        body = json.dumps({"login_name": f"s2-smoke-unknown-{secrets.token_hex(4)}", "password": "not-the-password"}).encode("utf-8")
        for _ in range(count):
            answers.append(self.client.request(
                "POST", "/api/session", headers={**CSRF, "Content-Type": "application/json", "X-Forwarded-For": forwarded},
                body=body, with_cookie=False,
            ))
        return answers

    def rate_limit_observed(self, answers):
        origin = answers[0].started
        return [
            {"n": i + 1, "status": a.status, "start_s": round(a.started - origin, 3), "end_s": round(a.ended - origin, 3),
             "sign_in_failed": (a.json().get("sign_in_failed") if a.status == 401 else None)}
            for i, a in enumerate(answers)
        ]

    def assert_burst(self, answers):
        if answers[6].started - answers[0].started >= 6:
            raise CaseInvalid("invalid (host too slow): request 7 started >= 6 s after request 1")
        for answer in answers[:6]:
            check(answer.status == 401 and answer.json().get("sign_in_failed") is True, "requests 1-6 are not backend 401 sign_in_failed")
        seventh = answers[6]
        check(seventh.status == 429, "request 7 is not 429")
        check(seventh.json().get("rate_limited") is True, "429 is not web's JSON")
        check(seventh.header("retry-after") == "60", "429 lacks Retry-After: 60")

    def sm10_sm11(self):
        with self.case("SM-10") as observed:
            answers = self.burst("198.51.100.7")
            observed["requests"] = self.rate_limit_observed(answers)
            observed["seventh"] = answers[6].summary("retry-after", "content-type")
            self.assert_burst(answers)
        with self.case("SM-11") as observed:
            answers = self.burst("203.0.113.1, 198.51.100.9")
            observed["a"] = self.rate_limit_observed(answers)
            self.assert_burst(answers)
            swapped = self.burst("198.51.100.9, 203.0.113.1", count=1)[0]
            observed["b"] = {"status": swapped.status}
            check(swapped.status == 401, "(b) the first X-Forwarded-For address was used as the key")
            reads = [self.client.request("GET", "/api/session", headers={"X-Forwarded-For": "198.51.100.9"}, with_cookie=False).status for _ in range(10)]
            observed["c"] = reads
            check(429 not in reads, "(c) GET /api/session was limited")

    def sm19(self):
        with self.case("SM-19") as observed:
            host = self.client.request("GET", "/api/health/", headers={"X-Forwarded-Proto": "https"})
            observed["a"] = host.summary("location")
            check(host.status == 307 and (host.header("location") or "").startswith("https://"), "(a) trusted hop scheme not honoured")
            script = (
                "import http.client; c = http.client.HTTPConnection('web', 80, timeout=10);"
                " c.request('GET', '/api/health/', headers={'X-Forwarded-Proto': 'https'}); r = c.getresponse();"
                " print(r.status, r.getheader('Location'))"
            )
            result = self.run(["docker", "run", "--rm", "--network", f"{self.project}_edge", BACKEND_IMAGE, "python", "-c", script])
            status, _, location = result.stdout.strip().partition(" ")
            observed["b"] = {"status": status, "location": location}
            check(status == "307" and location.startswith("http://"), "(b) X-Forwarded-Proto from an untrusted peer was honoured")

    def sm22(self):
        with self.case("SM-22") as observed:
            explicit = self.compose("run", "--rm", "--no-deps", "-T", "-e", "PARTFLOW_TRUSTED_PROXY=192.0.2.10", "web", "nginx", "-T", check_rc=False)
            output = explicit.stdout + explicit.stderr
            real_ip = re.findall(r"^\s*set_real_ip_from\s+(\S+);", output, re.MULTILINE)
            observed["explicit"] = {"rc": explicit.returncode, "set_real_ip_from": real_ip, "geo_entry": bool(re.search(r"192\.0\.2\.10 1;", output))}
            check(explicit.returncode == 0 and real_ip == ["192.0.2.10"], "explicit override not applied")
            check(re.search(r"192\.0\.2\.10 1;", output) is not None, "geo entry missing")
            invalid = self.compose("run", "--rm", "--no-deps", "-T", "-e", "PARTFLOW_TRUSTED_PROXY=not-an-ip", "web", "nginx", "-T", check_rc=False)
            observed["invalid"] = {"rc": invalid.returncode, "output_tail": (invalid.stdout + invalid.stderr)[-300:]}
            check(invalid.returncode != 0 and "PartFlow web: PARTFLOW_TRUSTED_PROXY must be one IPv4 address." in invalid.stderr + invalid.stdout, "invalid override accepted")
            isolated = self.run(["docker", "run", "--rm", "--network", "none", WEB_IMAGE, "nginx", "-T"], check_rc=False)
            observed["no_route"] = {"rc": isolated.returncode, "output_tail": (isolated.stdout + isolated.stderr)[-300:]}
            check(
                isolated.returncode != 0 and "PartFlow web: cannot determine the trusted proxy address" in isolated.stderr + isolated.stdout,
                "a container without a default route started",
            )

    def recreate_with_two_workers(self):
        with self.case("two-worker recreate") as observed:
            self.compose("up", "-d", "backend", timeout=300)
            observed["health"] = self.wait_for_health(60).status

    def import_file(self, quantity, count=IMPORT_WORK_ORDERS, prefix="S2SMOKE", pn_prefix="S2-SMOKE-PN"):
        rows = "".join(
            f"{prefix}-{index:05d},{pn_prefix}-{index:05d},{quantity},," + "\r\n" for index in range(1, count + 1)
        )
        return (IMPORT_HEADER + rows).encode("utf-8")

    def run_import(self, body, expected, count=IMPORT_WORK_ORDERS):
        headers = {**CSRF, "Content-Type": "text/csv"}
        started = time.monotonic()
        preview = self.client.request("POST", "/api/work-orders/import/preview", headers=headers, body=body, timeout=200)
        check(preview.status == 200, f"preview answered {preview.status}: {preview.excerpt()}")
        report = preview.json()
        check(report["check_token"] == hashlib.sha256(body).hexdigest(), "check token is not the body digest")
        check(report["summary"][f"will_{expected}"] == count, f"preview summary {report['summary']}")
        commit_headers = {**headers, "X-PartFlow-Import-Check": report["check_token"]}
        if report.get("update_token"):
            commit_headers["X-PartFlow-Import-Confirm"] = report["update_token"]
        committed = self.client.request("POST", "/api/work-orders/import", headers=commit_headers, body=body, timeout=200)
        result = {
            "bytes": len(body), "preview_status": preview.status, "preview_seconds": round(preview.ended - preview.started, 2),
            "import_status": committed.status, "import_seconds": round(committed.ended - committed.started, 2),
            "total_seconds": round(time.monotonic() - started, 2),
        }
        check(committed.status == 200, f"import answered {committed.status}: {committed.excerpt()}")
        data = committed.json()
        result["summary"] = data["summary"]
        check("server_unavailable" not in data, "web answered instead of the backend")
        past = {"create": "created", "update": "updated"}[expected]
        check(data["summary"][past] == count, f"import summary {data['summary']}")
        return result

    def sm21(self):
        with self.case("SM-21") as observed:
            check(self.user is not None, "not signed in (SM-6 failed)")
            # The seeded Administrator role may edit but not create Work Orders; the smoke grants the create key.
            grant = self.client.request(
                "PATCH", f"/api/roles/{self.user['role_id']}", headers={**CSRF, "Content-Type": "application/json"},
                body=json.dumps({"grant_permissions": ["MANAGE_WORK_ORDERS"]}).encode("utf-8"),
            )
            observed["grant_status"] = grant.status
            check(grant.status == 200, f"granting MANAGE_WORK_ORDERS answered {grant.status}: {grant.excerpt()}")
            first = self.import_file(10)
            check(len(first) <= MIB, "import file exceeds 1 MiB")
            observed["create"] = self.run_import(first, "create")
            observed["change_quantity_pf2"] = self.run_import(self.import_file(11), "update")
            slow = [k for k in ("create", "change_quantity_pf2") if observed[k]["import_seconds"] >= 120]
            if slow:
                raise CaseFailure(f"risk for the 180 s timeout: {slow} took >= 120 s")

    def sm16(self):
        with self.case("SM-16") as observed:
            result = self.compose("run", "--rm", "--no-deps", "-T", "backend", "python", "-m", "app.cli", "reconcile", check_rc=False, timeout=300)
            observed["rc"] = result.returncode
            report = json.loads(result.stdout)
            observed["exit_code"] = report.get("exit_code")
            observed["result"] = report.get("result")
            check(result.returncode == 0 and report.get("exit_code") == 0, "reconcile did not pass")

    def web_state(self):
        container = self.container("web")
        info = self.inspect(container)
        return {"container": container, "started_at": info["State"]["StartedAt"], "restart_count": info["RestartCount"]}

    def sm14(self):
        with self.case("SM-14") as observed:
            before = self.web_state()
            self.compose("stop", "backend", timeout=300)
            shell = self.client.request("GET", "/")
            observed["shell_status"] = shell.status
            check(shell.status == 200, "web did not serve the shell while backend is stopped")
            seen, origin, final = [], time.monotonic(), None
            while time.monotonic() - origin < 40:
                answer = self.client.request("GET", "/api/health", timeout=20)
                body = answer.json() if answer.body.startswith(b"{") else {}
                seen.append({"t_s": round(answer.started - origin, 1), "status": answer.status, "server_unavailable": body.get("server_unavailable")})
                check(answer.status in (502, 504) and body.get("server_unavailable") is True, f"unexpected answer while stopped: {answer.status} {answer.excerpt()}")
                if answer.status == 502:
                    final = answer
                    break
                time.sleep(1)
            observed["while_stopped"] = seen
            check(final is not None, "no JSON 502 within 40 s of stopping backend")
            self.compose("start", "backend", timeout=120)
            observed["after_start_seconds"] = self.seconds_to_health(30)
            self.compose("up", "-d", "--force-recreate", "backend", timeout=300)
            observed["after_recreate_seconds"] = self.seconds_to_health(30)
            after = self.web_state()
            observed["web_before"], observed["web_after"] = before, after
            check(before == after, "web was restarted")

    def seconds_to_health(self, limit):
        origin = time.monotonic()
        while time.monotonic() - origin < limit:
            try:
                if self.client.request("GET", "/api/health", timeout=10).status == 200:
                    return round(time.monotonic() - origin, 1)
            except OSError:
                pass
            time.sleep(1)
        raise CaseFailure(f"/api/health not 200 within {limit} s")

    def sm17(self):
        for case_id, workers in (("SM-17a", "1"), ("SM-17b", "2")):
            with self.case(case_id) as observed:
                started = time.monotonic()
                result = self.compose(
                    "run", "--rm", "--no-deps", "-T", "-e", f"WEB_CONCURRENCY={workers}", "backend",
                    env_file=self.empty_env_file, check_rc=False, timeout=90,
                )
                elapsed = time.monotonic() - started
                output = result.stdout + result.stderr
                credential_lines = [line for line in output.splitlines() if CREDENTIAL_URL.search(line)]
                observed.update({
                    "rc": result.returncode, "seconds": round(elapsed, 1),
                    "message_present": EMPTY_PASSWORD_FILE_MESSAGE in output,
                    "credential_url_lines": len(credential_lines),
                    "lines_with_scheme": len([line for line in output.splitlines() if "://" in line]),
                })
                check(elapsed < 60, "the refused backend did not end within 60 s")
                check(EMPTY_PASSWORD_FILE_MESSAGE in output, "the configuration message is missing")
                check(not credential_lines, "an output line carries a credential URL")
                if workers == "1":
                    check(result.returncode == 3, f"one worker exited {result.returncode}, not 3")
                else:
                    check(MULTI_WORKER_STOP in output, "the supervisor did not stop after the failed worker")
        with self.case("SM-17c") as observed:
            # An empty owner password stops both one-shot jobs before they connect (P16-S4).
            attempts = {
                "migrate": ("migrate", "--no-backup-reason", "s4 smoke sm-17c"),
                "db-roles": ("db-roles",),
            }
            for label, arguments in attempts.items():
                result = self.compose(
                    "--profile", "ops", "run", "--rm", "--no-deps", "-T", *arguments,
                    env_file=self.empty_owner_env_file, check_rc=False, timeout=120,
                )
                output = result.stdout + result.stderr
                report = parse_report(result.stdout)
                credential_lines = [line for line in output.splitlines() if CREDENTIAL_URL.search(line)]
                observed[label] = {
                    "rc": result.returncode, "result": report.get("result"), "error_code": (report.get("error") or {}).get("code"),
                    "credential_url_lines": len(credential_lines),
                }
                check(result.returncode == 2, f"{label} exited {result.returncode}, not 2")
                check(report.get("result") == "failed", f"{label} did not print one JSON document with result failed")
                check((report.get("error") or {}).get("code") == "configuration_invalid", f"{label} is not configuration_invalid")
                check(not credential_lines, f"an output line of {label} carries a credential URL")

    # -- P16-S4: database roles --------------------------------------------------

    def owner_psql(self, sql, *options, check_rc=True):
        """psql as the owner inside db; the variables expand inside the container (the host has none)."""
        script = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" ' + " ".join(options) + f' -c "{sql}"'
        return self.compose("exec", "-T", "db", "sh", "-c", script, check_rc=check_rc, timeout=120)

    def sm29_to_sm33(self):
        with self.case("SM-29") as observed:
            first = parse_report(self.first_db_roles.stdout)
            second_run = self.compose("--profile", "ops", "run", "--rm", "-T", "db-roles", check_rc=False, timeout=300)
            second = parse_report(second_run.stdout)
            observed["first"] = {"rc": self.first_db_roles.returncode, "result": first.get("result"), "roles": first.get("roles")}
            observed["second"] = {"rc": second_run.returncode, "result": second.get("result"), "roles": second.get("roles")}
            for label, run, report, action in (("first", self.first_db_roles, first, "created"), ("second", second_run, second, "unchanged")):
                check(run.returncode == 0 and report.get("result") == "provisioned", f"{label} db-roles is not provisioned")
                roles = report.get("roles") or []
                check([r.get("name") for r in roles] == [APP_ROLE, "partflow_maintenance"], f"{label} db-roles roles {roles}")
                check(all(r.get("action") == action and r.get("password") == "set" for r in roles), f"{label} db-roles is not {action}")
        with self.case("SM-30") as observed:
            report = parse_report(self.first_migrate.stdout)
            grants = report.get("grants") or {}
            observed.update({"rc": self.first_migrate.returncode, "result": report.get("result"), "grants": grants})
            check(self.first_migrate.returncode == 0 and report.get("result") == "upgraded", "the first migrate did not upgrade")
            check(grants.get("status") == "applied" and grants.get("tables") == CLASSIFIED_TABLES, "migrate did not apply the grants")
        with self.case("SM-31") as observed:
            check(self.client.request("GET", "/api/health").status == 200, "health is not 200")
            result = self.owner_psql("SELECT usename FROM pg_stat_activity WHERE application_name = 'partflow-api'", "-At")
            users = result.stdout.split()
            observed["usenames"] = users
            check(users, "no partflow-api session in pg_stat_activity")
            check(all(user == APP_ROLE for user in users), f"a partflow-api session is not {APP_ROLE}")
        with self.case("SM-32") as observed:
            cells = {}
            for table in GUARDED_TABLES:
                for verb, statement in PROBE_STATEMENTS.items():
                    sql = statement.format(table=table)
                    script = (
                        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -c BEGIN'
                        f' -c "SET LOCAL ROLE {APP_ROLE}" -c "{sql}" -c ROLLBACK'
                    )
                    result = self.compose("exec", "-T", "db", "sh", "-c", script, check_rc=False, timeout=120)
                    allowed = PROBE_ALLOWED.get((table, verb))
                    if allowed:
                        verdict = "allowed" if allowed in result.stdout and "ERROR" not in result.stderr else "wrong"
                    else:
                        verdict = "refused" if f"permission denied for table {table}" in result.stderr else "wrong"
                    cells[f"{table} {verb}"] = {
                        "expected": "allowed" if allowed else "refused", "verdict": verdict, "psql_rc": result.returncode,
                        "stderr": result.stderr.strip()[-200:],
                    }
            observed["cells"] = cells
            wrong = [cell for cell, value in cells.items() if value["verdict"] == "wrong"]
            check(not wrong, f"cells not as expected: {wrong}")
        with self.case("SM-33") as observed:
            result = self.compose("run", "--rm", "--no-deps", "-T", "backend", "python", "-m", "app.cli", "reconcile", "--check", "h",
                                  check_rc=False, timeout=300)
            report = parse_report(result.stdout)
            h = next((c for c in report.get("checks") or [] if c.get("id") == "h"), {})
            database = report.get("database") or {}
            observed.update({
                "rc": result.returncode, "h_status": h.get("status"), "h_examined": h.get("examined"),
                "h_findings": h.get("findings"), "connected_role": database.get("connected_role"),
                "connected_role_superuser": database.get("connected_role_superuser"),
            })
            check(result.returncode == 0 and report.get("exit_code") == 0, f"reconcile --check h exited {result.returncode}")
            check(h.get("status") == "pass", f"check (h) is {h.get('status')}")
            check(database.get("connected_role") == APP_ROLE, "reconcile is not connected as partflow_app")
            check(database.get("connected_role_superuser") is False, "reconcile is connected as a superuser")

    def sm34(self):
        with self.case("SM-34") as observed:
            live = values_of(self.env_file).get("POSTGRES_DB")
            check(RESTORE_DATABASE != live, f"the restore database name equals the live database {live}")
            dump = "/tmp/partflow-s4-restore.dump"
            try:
                steps = {
                    "pg_dump": self.compose("exec", "-T", "db", "sh", "-c",
                                            f'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges -f {dump}',
                                            check_rc=False, timeout=300),
                    "createdb": self.compose("exec", "-T", "db", "sh", "-c", f'createdb -U "$POSTGRES_USER" {RESTORE_DATABASE}',
                                             check_rc=False, timeout=120),
                }
                steps["pg_restore"] = self.compose(
                    "exec", "-T", "db", "sh", "-c",
                    f'pg_restore -U "$POSTGRES_USER" -d {RESTORE_DATABASE} --exit-on-error --no-owner --no-privileges {dump}',
                    check_rc=False, timeout=300,
                )
                steps["apply-grants"] = self.compose("--profile", "ops", "run", "--rm", "-T", "-e", f"DATABASE_NAME={RESTORE_DATABASE}",
                                                     "db-roles", "apply-grants", check_rc=False, timeout=300)
                steps["reconcile"] = self.compose("run", "--rm", "--no-deps", "-T", "-e", f"DATABASE_NAME={RESTORE_DATABASE}", "backend",
                                                  "python", "-m", "app.cli", "reconcile", "--check", "h", check_rc=False, timeout=300)
                observed.update({name: result.returncode for name, result in steps.items()})
                grants = parse_report(steps["apply-grants"].stdout)
                reconcile = parse_report(steps["reconcile"].stdout)
                h = next((c for c in reconcile.get("checks") or [] if c.get("id") == "h"), {})
                observed.update({"grants": grants.get("grants"), "h_status": h.get("status"), "h_findings": h.get("findings")})
                for name in ("pg_dump", "createdb", "pg_restore"):
                    check(steps[name].returncode == 0, f"{name} exited {steps[name].returncode}")
                check(steps["apply-grants"].returncode == 0 and grants.get("result") == "applied", "apply-grants on the restore failed")
                check(steps["reconcile"].returncode == 0 and h.get("status") == "pass", "(h) does not pass on the restore")
            finally:
                cleanup = self.compose("exec", "-T", "db", "sh", "-c",
                                       f'dropdb -U "$POSTGRES_USER" --if-exists {RESTORE_DATABASE}; rm -f {dump}',
                                       check_rc=False, timeout=120)
                observed["cleanup_rc"] = cleanup.returncode

    def sm36(self):
        with self.case("SM-36") as observed:
            old = self.read_secret("partflow_app_password")
            new = secrets.token_urlsafe(24)
            check(new not in (old, self.read_secret("partflow_maintenance_password")), "the new password is not distinct")
            self.replace_secret("partflow_app_password", new)
            self.compose("stop", "backend", timeout=300)
            rotate = self.compose("--profile", "ops", "run", "--rm", "-T", "db-roles", check_rc=False, timeout=300)
            report = parse_report(rotate.stdout)
            observed["db_roles"] = {"rc": rotate.returncode, "result": report.get("result"), "roles": report.get("roles")}
            self.compose("up", "-d", "--force-recreate", "--no-deps", "backend", timeout=300)
            observed["health_seconds"] = self.seconds_to_health(30)
            check(rotate.returncode == 0 and report.get("result") == "provisioned", "db-roles did not rotate the password")
            check(all(r.get("action") == "unchanged" and r.get("password") == "set" for r in report.get("roles") or []),
                  "db-roles is not unchanged/set")
            check(self.user is not None, "not signed in (SM-6 failed)")
            observed["write"] = self.run_import(self.import_file(5, count=5, prefix="S4ROT", pn_prefix="S4-ROT-PN"), "create", count=5)
            # Over the container's network address (scram-sha-256; the image trusts the socket and loopback). The
            # password arrives on stdin; tr drops the line break (a Windows host's text stdin sends CR LF).
            login = 'PGPASSWORD=$(tr -d "\\r\\n") psql -h db -U ' + APP_ROLE + ' -d "$POSTGRES_DB" -At -c "SELECT current_user"'
            with_old = self.compose("exec", "-T", "db", "sh", "-c", login, check_rc=False, timeout=60, input_text=old + "\n")
            with_new = self.compose("exec", "-T", "db", "sh", "-c", login, check_rc=False, timeout=60, input_text=new + "\n")
            observed["login_old"] = {"rc": with_old.returncode, "stderr": with_old.stderr.strip()[-200:]}
            observed["login_new"] = {"rc": with_new.returncode, "stdout": with_new.stdout.strip(), "stderr": with_new.stderr.strip()[-200:]}
            check(with_old.returncode != 0 and "password authentication failed" in with_old.stderr, "the old password is accepted")
            check(with_new.returncode == 0 and with_new.stdout.strip() == APP_ROLE, "the new password is refused")

    def sm35(self):
        with self.case("SM-35") as observed:
            flows = ("SM-6", "SM-7", "SM-8", "SM-9", "SM-21", "SM-31", "SM-36")
            statuses = {case_id: (self.evidence["cases"].get(case_id) or {}).get("status") for case_id in flows}
            observed["statuses"] = statuses
            check(all(status == "pass" for status in statuses.values()), "a write flow through web did not pass as partflow_app")

    def sm20(self):
        self.evidence["cases"]["SM-20"] = {
            "status": "manual",
            "observed": {
                "url": f"http://127.0.0.1:{self.port}/",
                "note": "Browser check on the kept stack (--keep): load / and /management/work-orders, sign in,"
                " open an image upload preview; no CSP violation in the console. Recorded separately.",
                "kept": self.args.keep,
            },
        }

    # -- teardown -------------------------------------------------------------

    def teardown(self):
        if not self.created:
            return
        if self.args.keep:
            self.evidence["kept"] = True
            print(
                f"Kept: http://127.0.0.1:{self.port}/  (administrator login s2-smoke-admin; the password is in"
                f" {self.workdir / 'admin-password'} for the manual SM-20 check)\n"
                f"Tear down: docker compose -p {self.project} -f {COMPOSE_FILE} --env-file {self.env_file} down -v --remove-orphans",
                flush=True,
            )
            if getattr(self, "admin_password", None):
                (self.workdir / "admin-password").write_text(self.admin_password + "\n", encoding="utf-8")
            return
        with contextlib.suppress(CaseFailure):
            self.compose("down", "-v", "--remove-orphans", timeout=600)

    def remove_workdir(self):
        if self.args.keep and self.created:
            return
        for path in self.workdir.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o600)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def main(self):
        self.refuse_foreign_project()
        self.prepare()
        outcome = 1
        try:
            self.start()
            self.record_versions()
            self.sm1_to_sm5()
            self.sm24_to_sm28()
            self.sm12_sm13_sm15()
            self.sm19()
            self.sm22()
            self.sm18(1)
            self.sm6_first_run()
            self.recreate_with_two_workers()
            self.sm18(2)
            self.sm7_sm8_sm9()
            self.sm10_sm11()
            self.sm21()
            self.sm16()
            self.sm29_to_sm33()
            self.sm34()
            self.sm14()
            self.sm17()
            self.sm36()
            self.sm35()
            self.sm20()
            statuses = [c["status"] for c in self.evidence["cases"].values()]
            outcome = 1 if "fail" in statuses else 2 if "invalid" in statuses else 0
        except (CaseFailure, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            self.evidence["run_error"] = f"{type(exc).__name__}: {exc}"
            print(f"run error: {exc}", file=sys.stderr, flush=True)
        finally:
            self.teardown()
            self.remove_workdir()
            self.evidence["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            self.evidence["outcome"] = {0: "pass", 1: "fail", 2: "invalid (re-run)"}[outcome]
            evidence_path = Path(self.args.evidence)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(self.evidence, indent=2) + "\n", encoding="utf-8")
            print(f"evidence: {evidence_path} ({self.evidence['outcome']})", flush=True)
        return outcome


def posix_host_path(path):
    """An absolute host path as deploy/production/*.sh require it (leading "/"; the MSYS form /c/... on Windows, which
    Docker Desktop also accepts as a bind source)."""
    text = Path(path).resolve().as_posix()
    if os.name == "nt" and re.match(r"^[A-Za-z]:/", text):
        return "/" + text[0].lower() + text[2:]
    return text


def make_backup_dir(parent):
    """A temporary PARTFLOW_BACKUP_DIR (P16-S5), mode 0700, removed with its parent work directory."""
    path = Path(parent) / "backups"
    path.mkdir()
    os.chmod(path, 0o700)
    return path


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def parse_report(text):
    """The one JSON document an app.cli command prints on stdout ({} when stdout is not exactly one object)."""
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def values_of(env_file):
    values = {}
    for line in Path(env_file).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def display(command, env_extra=None):
    prefix = " ".join(f"{k}={v}" for k, v in (env_extra or {}).items())
    return (prefix + " " if prefix else "") + " ".join(command)


def describe(value):
    if isinstance(value, Answer):
        return f"HTTP {value.status} {value.excerpt(120)}"
    return repr(value)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="P16-S2/S3/S4 production stack smoke (throwaway Compose project).")
    parser.add_argument("--evidence", required=True, help="path of the evidence JSON to write")
    parser.add_argument("--keep", action="store_true", help="leave the stack running for the manual SM-20 browser check")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f"Compose project name (default {DEFAULT_PROJECT})")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Smoke(parse_args(sys.argv[1:])).main())
