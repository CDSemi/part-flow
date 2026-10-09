"""P16-S6 observability rehearsal: real images, synthetic data, one throwaway Compose project (P16-S6 SPEC section 6.4,
cases OR-1..OR-16; OR-12 is the separate stack_smoke.py / release_rehearsal.py / backup_rehearsal.py run, recorded by
the caller).

Usage (from any directory, on a host with the docker CLI, Compose v2, git, curl, openssl, python3 and a POSIX sh):
  python deploy/production/tests/observability_rehearsal.py --evidence <path.json> [--log-dir DIR] [--keep]

Releases, built from temporary copies of the working tree (the repository is never modified) with
PARTFLOW_COMMIT=$(git rev-parse HEAD): A = the checkout; F = A + the rehearsal-only revision 9997_s5_rehearsal_finding
of backup_rehearsal.py (one deliberate reconcile check (h) finding, OR-10 only, run last).

Cases: OR-1 install, synthetic data through web, a daily backup, check.sh all green; OR-2 the request id end to end;
OR-3 every backend/web/db log line (read after OR-14) against the secrets of the run; OR-4 web stopped, re-notification,
the one-off reconcile container; OR-5 a restart; OR-6 the database volume filled; OR-7 the certificate check against a
throwaway TLS nginx; OR-8 a stale backup; OR-9 the log volume of 1,000 recorded station commands; OR-10
scheduled-reconcile.sh clean, then the (h) finding; OR-11 status under a table lock; OR-13 a schema mismatch; OR-14 an
unhandled 500 and the errors check; OR-15 the disk notification-test form; OR-16 the release guard. The (h) part of
OR-10 switches the stack to F; OR-14 (stopping db discards the tmpfs volume, so the empty schema is re-installed after
it) and OR-3 run last.

Isolation: only the project `partflow-s6-rehearsal` (edge 172.30.249.0/24, a free loopback port, its own volume with
the size-limited tmpfs override below, generated env file, secrets, backup, records and state directories) and the
OR-7 container `pf-s6-or7-*` it starts itself. It refuses a project that already has containers or volumes, never
touches the development stack `partflow`, other projects or containers it did not start, or the OPS lane. In `finally`
it runs `down -v --remove-orphans` for its project, removes the OR-7 container and its image tags (A, F). It never
calls `sync`.

Every command runs from a rehearsal checkout in the work directory: a copy of compose.production.yaml whose
postgres_data volume is a 256 MiB tmpfs (OR-6; the only change, so that every Compose command of the project, the
scripts' included, sees one volume configuration) and a copy of deploy/production (the scripts under test).

Host adaptations (Docker Desktop on Windows, as backup_rehearsal.py; none is used on a Linux host):
- an `id` shim printing 0 for -u/-g (bind-mounted Windows folders show as root-owned inside containers);
- a `python3` wrapper of this interpreter (the Windows `python3` may be the Store alias);
- a `df` shim answering for the Docker root inside the Docker VM (`df -Pk /` in a throwaway postgres container);
- a `timeout` wrapper of Git's coreutils timeout (C:\\Windows\\System32\\timeout.exe is also on PATH);
- MSYS2_ARG_CONV_EXCL=/var/lib/postgresql so that Git Bash passes check.sh's `exec db df -Pk /var/lib/postgresql/data`
  unconverted to docker.exe.

Evidence JSON: per case pass / fail / not_run with the observed values (never a password, token, badge or cookie
value), the commands with exit codes and durations. Exit 0 = every automated case passed; 1 = a case failed or the run
broke.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_rehearsal import FINDING_FUNCTION, FINDING_REVISION, FINDING_TEMPLATE, BackupRehearsal  # noqa: E402
from release_rehearsal import ROLE_SECRETS, TOKEN_PATTERN, Rehearsal, alembic_head, copy_checkout, write_secret_files  # noqa: E402
from stack_smoke import CaseFailure, check, free_port, make_backup_dir, parse_report, posix_host_path  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
OR7_CONF = Path(__file__).resolve().parent / "fixtures" / "or7-tls.conf"
DEFAULT_PROJECT = "partflow-s6-rehearsal"
EDGE_SUBNET = "172.30.249.0/24"
RELEASES = {"A": "s6-or-a", "F": "s6-or-f"}
FORBIDDEN = ("partflow", "partflow-production", "partflow-staging")
NGINX_IMAGE = "nginx:1.30.5-alpine"
VOLUMES = "\nvolumes:\n    postgres_data:\n"
TMPFS_VOLUMES = """
volumes:
    # P16-S6 observability rehearsal only: a size-limited tmpfs (OR-6).
    postgres_data:
        driver_opts:
            type: tmpfs
            device: tmpfs
            o: "size=256m"
"""
CHECK_IDS = ("https", "certificate", "containers", "restarts", "errors", "disk_data", "disk_backup", "disk_docker",
             "disk_archive", "database", "schema", "backup_age", "archival_proposal")
STATUS_IDS = ("database", "schema", "backup_age", "archival_proposal")
COMMANDS_OR9 = 1000
UNKNOWN_REVISION = "9999_s6_rehearsal_unknown"
# Non-JSON lines a container may print outside its request log: the nginx image's entrypoint and nginx's own error log.
WEB_NON_ACCESS = re.compile(
    r"^(/docker-entrypoint\.(sh|d/)|\d{2}-[A-Za-z0-9._-]+\.(sh|envsh): |\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} \[(notice|warn|error|info)\])")


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ObservabilityRehearsal(BackupRehearsal):
    releases = RELEASES
    edge_subnet = EDGE_SUBNET

    def __init__(self, args, evidence):
        super().__init__(args, evidence)
        self.or7_containers = []
        self.secret_values = {}
        self.first_backend_lines = []

    # -- setup ----------------------------------------------------------------

    def refuse_foreign_project(self):
        if self.project in FORBIDDEN or not self.project.startswith("partflow-s6-"):
            raise SystemExit(f"Refusing to rehearse as project {self.project!r}.")
        Rehearsal.refuse_foreign_project(self)

    def prepare(self):
        self.secrets_dir = self.workdir / "secrets"
        self.secrets_dir.mkdir()
        write_secret_files(self.secrets_dir, ("postgres_password", *ROLE_SECRETS))
        for name in ("postgres_password", *ROLE_SECRETS):
            self.secret_values[name] = (self.secrets_dir / name).read_text(encoding="utf-8").strip()
        self.backup_dir = make_backup_dir(self.workdir)
        self.env_file = self.workdir / "rehearsal.env"
        self.records = self.workdir / "records"
        self.records.mkdir()
        self.monitoring = self.workdir / "monitoring"
        self.checkout = self.workdir / "checkout"
        shutil.copytree(REPO / "deploy" / "production", self.checkout / "deploy" / "production",
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        compose_text = (REPO / "compose.production.yaml").read_text(encoding="utf-8")
        check(compose_text.count(VOLUMES) == 1, "compose.production.yaml volumes section not found")
        self.compose_file = self.checkout / "compose.production.yaml"
        self.compose_file.write_text(compose_text.replace(VOLUMES, TMPFS_VOLUMES), encoding="utf-8", newline="\n")
        self.check_sh_path = self.checkout / "deploy" / "production" / "check.sh"
        self.reconcile_sh_path = self.checkout / "deploy" / "production" / "scheduled-reconcile.sh"
        self.set_env(RELEASES["A"])
        self.head = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        source_a = self.workdir / "src-a"
        copy_checkout(source_a)
        self.code_head = alembic_head(source_a / "backend" / "alembic" / "versions")
        source_f = self.workdir / "src-f"
        shutil.copytree(source_a, source_f)
        (source_f / "backend" / "alembic" / "versions" / f"{FINDING_REVISION}.py").write_text(
            FINDING_TEMPLATE.format(revision=FINDING_REVISION, down=self.code_head, function=FINDING_FUNCTION),
            encoding="utf-8", newline="\n")
        self.sources = {"A": source_a, "F": source_f}
        self.df_shim = self.workdir / "shim"
        self.df_shim.mkdir()
        self.write_df_shim()
        self.write_id_shim()
        self.tools = self.workdir / "tools"
        self.tools.mkdir()
        wrapper = self.tools / "python3"
        wrapper.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
        os.chmod(wrapper, 0o755)
        adaptations = ["python3 wrapper", "df shim for the Docker root"]
        if os.name == "nt":
            adaptations.append("id shim (0 for -u/-g)")
            sh = shutil.which("sh")
            coreutils = Path(sh).parent / "timeout.exe" if sh else None
            check(coreutils is not None and coreutils.exists(), "no coreutils timeout beside Git's sh")
            timeout = self.tools / "timeout"
            timeout.write_text(f'#!/bin/sh\nexec "{coreutils.as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
            os.chmod(timeout, 0o755)
            adaptations += [f"timeout wrapper of {coreutils.as_posix()}", "MSYS2_ARG_CONV_EXCL=/var/lib/postgresql"]
        self.evidence.update({"port": self.port, "edge_subnet": EDGE_SUBNET, "commit": self.head, "code_head": self.code_head,
                              "host_adaptations": adaptations, "backup_dir": posix_host_path(self.backup_dir)})

    def environment(self, **extra):
        env = super().environment(**extra)
        if os.name == "nt":
            env["MSYS2_ARG_CONV_EXCL"] = "/var/lib/postgresql"
        return env

    def compose(self, *arguments, release=None, compose_file=None, **kwargs):
        """`$PF -p <project>` with the rehearsal checkout's compose.production.yaml (tmpfs database volume)."""
        env_extra = dict(kwargs.pop("env_extra", None) or {})
        if release is not None:
            env_extra["PARTFLOW_RELEASE"] = release
        command = ["docker", "compose", "-p", self.project, "-f", str(self.compose_file), "--env-file", str(self.env_file)]
        return self.run(command + list(arguments), env_extra=env_extra, **kwargs)

    def script(self, script, *arguments, env_extra=None, timeout=1800, shim=True):
        """A deploy/production script of the rehearsal checkout, run from that checkout (as on a host)."""
        env_extra = dict(env_extra or {})
        if not shim:
            env_extra["PATH"] = os.environ.get("PATH", "")
        path = self.checkout / "deploy" / "production" / Path(script).name
        return self.run(["sh", path.as_posix(), *arguments], timeout=timeout, check_rc=False, record_output=True,
                        env_extra=env_extra, cwd=self.checkout)

    # -- helpers --------------------------------------------------------------

    def write_headers(self, release=None, **extra):
        return {"X-PartFlow-CSRF": "1", "Content-Type": "application/json", "X-PartFlow-Release": release or self.serving, **extra}

    def api(self, method, path, body=None, status=None, station=None, cookie=True, **headers):
        sent = dict(headers)
        if method not in ("GET", "HEAD"):
            sent = {**self.write_headers(), **sent}
        if station is not None:
            sent["X-PartFlow-Station-Device"] = self.devices[station]
            cookie = False
        data = json.dumps(body).encode("utf-8") if body is not None else None
        answer = self.client.request(method, path, headers=sent, body=data, with_cookie=cookie)
        if status is not None:
            check(answer.status == status, f"{method} {path} answered {answer.status}: {answer.excerpt(300)}")
        return answer

    def check_sh(self, *arguments, state=None, env_file=None, url=None):
        """check.sh --rehearsal against the project; (exit, lines)."""
        state = state or self.monitoring
        args = ["--url", url or f"http://127.0.0.1:{self.port}", "--env-file", (env_file or self.env_file).as_posix(),
                "--state-dir", state.as_posix(), "--records-dir", posix_host_path(self.records),
                "--rehearsal", "--project", self.project, *arguments]
        result = self.run(["sh", self.check_sh_path.as_posix(), *args], timeout=900, check_rc=False, record_output=True,
                          cwd=self.checkout)
        self.save_log(f"check-{len(self.evidence['commands'])}.log", result.stdout + result.stderr)
        return result.returncode, result.stdout.splitlines()

    def line(self, lines, check_id):
        found = [text for text in lines if re.match(rf"^(PASS|FAIL|SKIP) {check_id} ", text)]
        check(len(found) == 1, f"no single {check_id} line in {lines}")
        return found[0]

    def status_run(self, env_file=None, *arguments):
        command = ["docker", "compose", "-p", self.project, "-f", str(self.compose_file), "--env-file",
                   str(env_file or self.env_file), "--profile", "ops", "run", "--rm", "--no-deps", "-T", "--user", "0:0"
                   if os.name == "nt" else f"{os.getuid()}:{os.getgid()}", "status", *arguments]
        result = self.run(command, timeout=300, check_rc=False)
        return result.returncode, parse_report(result.stdout), result.stderr

    def logs(self, service, since=None, until=None):
        """The docker logs of the service's one container (a stopped one included)."""
        ids = self.compose("ps", "-a", "-q", service).stdout.split()
        check(len(ids) == 1, f"expected one {service} container, found {ids}")
        container = ids[0]
        command = ["docker", "logs", container] + (["--since", since] if since else []) + (["--until", until] if until else [])
        result = self.run(command, timeout=300)
        return (result.stdout + result.stderr).splitlines()

    def json_lines(self, lines):
        parsed, other = [], []
        for text in lines:
            try:
                value = json.loads(text)
            except ValueError:
                other.append(text)
                continue
            if isinstance(value, dict):
                parsed.append(value)
            else:
                other.append(text)
        return parsed, other

    def wait_ready(self, seconds=180):
        self.wait_for(lambda a, b: getattr(a, "status", None) == 200 and b.get("schema") == "current", seconds,
                      "health 200 with schema current")

    def wait_container(self, service, seconds=180):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ids = self.compose("ps", "-q", service).stdout.split()
            if len(ids) == 1:
                state = self.inspect(ids[0])["State"]
                if state.get("Status") == "running" and (state.get("Health") or {}).get("Status") in (None, "healthy"):
                    return
            time.sleep(2)
        raise CaseFailure(f"{service} not running and healthy within {seconds} s")

    def owner_sql(self, sql):
        return self.psql(sql)

    def fresh_state(self, name):
        path = self.workdir / f"state-{name}"
        path.mkdir()
        return path

    # -- OR-1 -----------------------------------------------------------------

    def or1_install(self):
        with self.case("OR-1") as observed:
            for name in ("A", "F"):
                self.build(name)
            self.serving = RELEASES["A"]
            self.compose("up", "-d", "--wait", "db", timeout=300)
            self.compose("--profile", "ops", "run", "--rm", "-T", "db-roles", timeout=300)
            migrate = parse_report(self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason",
                                                "s6 rehearsal: first install", timeout=300).stdout)
            check(migrate.get("result") == "upgraded", f"first install migrate: {migrate.get('result')}")
            self.compose("up", "-d", "backend", "web", timeout=300, env_extra={"PARTFLOW_BACKEND_WORKERS": "1"})
            self.wait_ready(120)
            self.first_admin()
            self.compose("up", "-d", "backend", timeout=300)
            self.wait_ready(120)
            self.seed(observed)
            result, name = self.backup()
            check(result.returncode == 0 and name, f"backup.sh daily exit {result.returncode}: {result.stderr[-800:]}")
            self.daily = name
            observed["backup"] = name
            code, lines = self.check_sh()
            observed["check"] = {"exit": code, "lines": lines}
            check(code == 0, f"check.sh exit {code}")
            check([text.split(" ")[1] for text in lines if not text.startswith("NOTE")] == list(CHECK_IDS), "check ids/order")
            for text in lines:
                if text.startswith("NOTE"):
                    continue
                expected = "SKIP" if text.split(" ")[1] in ("certificate", "disk_archive", "archival_proposal") else "PASS"
                check(text.startswith(expected + " "), f"unexpected line {text}")

    def first_admin(self):
        status = self.client.request("GET", "/api/setup")
        setup = status.json()
        check(status.status == 200 and setup.get("open") is True, "setup is not open")
        tokens = TOKEN_PATTERN.findall(self.compose("logs", "--no-log-prefix", "backend").stdout)
        check(len(tokens) == 1, f"expected one announced setup token, found {len(tokens)}")
        self.secret_values["setup_token"] = tokens[0]
        role = next((r for r in setup["eligible_roles"] if r["name"] == "Administrator"), setup["eligible_roles"][0])
        self.admin_login = "s6-rehearsal-admin"
        self.admin_password = secrets.token_urlsafe(18)
        self.secret_values["admin_password"] = self.admin_password
        answer = self.api("POST", "/api/setup/administrator", {
            "setup_token": tokens[0], "login_name": self.admin_login, "display_name": "S6 Rehearsal",
            "role_id": role["id"], "password": self.admin_password}, status=201)
        self.client.remember_cookie(answer)
        self.secret_values["session_cookie"] = self.client.cookie.split("=", 1)[1]
        self.user = answer.json()["user"]
        # The one-worker container is replaced by the two-worker one below: keep its log for OR-3.
        self.first_backend_lines = self.logs("backend")
        wanted = ["MANAGE_DEPARTMENTS", "MANAGE_AREAS", "MANAGE_OPERATIONS", "MANAGE_WORKERS", "MANAGE_SCAN_STATIONS",
                  "MANAGE_WORKER_SESSION_POLICIES", "MANAGE_CORRECTION_PERMISSIONS", "MANAGE_WORK_ORDERS",
                  "MANAGE_PART_NUMBER_MASTER", "VIEW_PRODUCTION_DATA"]
        grant = self.api("PATCH", f"/api/roles/{self.user['role_id']}", {"grant_permissions": wanted})
        if grant.status != 200:
            wanted.remove("MANAGE_CORRECTION_PERMISSIONS")
            self.api("PATCH", f"/api/roles/{self.user['role_id']}", {"grant_permissions": wanted}, status=200)

    def unique(self, prefix):
        return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

    def cell(self, mode="DISABLED"):
        department = self.api("POST", "/api/departments", {"name": self.unique("S6-DEPT")}, status=201).json()
        area = self.api("POST", "/api/areas", {"department_id": department["id"], "name": self.unique("S6-AREA"),
                                               "worker_identification_mode": mode}, status=201).json()
        operation = self.api("POST", "/api/operations", {"area_id": area["id"], "code": self.unique("OP")}, status=201).json()
        station = self.api("POST", "/api/scan-stations", {"station_id": self.unique("ST"), "area_id": area["id"]},
                           status=201).json()
        station_id = str(station["station_id"])
        issued = self.api("POST", f"/api/scan-stations/{station_id}/device-enrollments", {"label": "s6 rehearsal"},
                          status=201).json()
        code = str(issued["enrollment_code"])
        self.secret_values.setdefault("enrollment_codes", []).append(code)
        activated = self.api("POST", f"/api/scan-stations/{station_id}/device-activations", {"enrollment_code": code},
                             status=201, cookie=False).json()
        self.devices[station_id] = str(activated["device_token"])
        self.secret_values.setdefault("device_tokens", []).append(self.devices[station_id])
        return {"area_id": int(area["id"]), "operation_id": int(operation["id"]), "station_id": station_id}

    def release_flow(self, cell, quantity=10):
        pn = self.unique("S6-PN")
        work_order = self.api("POST", "/api/work-orders", {"lines": [{"part_number": pn, "requested_quantity": 500}]},
                              status=201).json()
        released = self.api("POST", f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release", {
            "part_number": pn, "quantity": quantity, "route_mode": "FLOATING", "starting_area_id": cell["area_id"],
            "operation_id": cell["operation_id"], "confirm_active_quantity": False, "device_event_id": str(uuid.uuid4())},
            status=201).json()
        return int(released["quantity_flow_id"]), pn

    def transfer(self, flow, pn, source, target, quantity, status=201, **headers):
        body = {"part_number": pn, "quantity_flow_id": flow, "source_area_id": source["area_id"],
                "target_area_id": target["area_id"], "quantity": quantity, "device_event_id": str(uuid.uuid4())}
        return self.api("POST", f"/api/scan-stations/{target['station_id']}/transfers", body, status=status,
                        station=target["station_id"], **headers), body

    def seed(self, observed):
        """Synthetic data through web: Areas, Scan Stations with enrolled devices, a Worker signed in by badge, a
        released flow, transfers, a refused command and a refused duplicate-badge Worker."""
        self.devices = {}
        self.api("PUT", "/api/policies/worker-sessions", {"worker_session_timeout_minutes": 60, "badge_confirm_done": False,
                                                          "badge_confirm_queue": False, "badge_confirm_undo": False},
                 status=200)
        self.cell_a = self.cell()
        self.cell_b = self.cell("SCANNED")
        badge = self.unique("S6-BADGE")
        self.secret_values["badges"] = [badge]
        self.worker = self.api("POST", "/api/workers", {"name": self.unique("S6 Worker"), "badge_barcode": badge},
                               status=201).json()
        duplicate = self.api("POST", "/api/workers", {"name": self.unique("S6 Worker"), "badge_barcode": badge})
        observed["duplicate_badge_status"] = duplicate.status
        check(400 <= duplicate.status < 500, f"the duplicate badge was not refused ({duplicate.status})")
        signed = self.api("POST", f"/api/scan-stations/{self.cell_b['station_id']}/badge-scans", {"badge": badge},
                          status=200, station=self.cell_b["station_id"]).json()
        check(signed.get("outcome") == "SIGNED_IN", f"badge scan outcome {signed.get('outcome')}")
        self.flow, self.pn = self.release_flow(self.cell_a)
        self.flow_area = self.cell_a
        created, _ = self.transfer(self.flow, self.pn, self.cell_a, self.cell_b, 10)
        self.flow_area = self.cell_b
        refused, _ = self.transfer(self.flow, self.pn, self.cell_a, self.cell_b, 10, status=None)
        observed["refused_status"] = refused.status
        check(400 <= refused.status < 500, f"the second transfer from A was not refused ({refused.status})")
        observed["seed"] = {"areas": [self.cell_a["area_id"], self.cell_b["area_id"]], "flow": self.flow,
                            "created_status": created.status}

    # -- OR-2 -----------------------------------------------------------------

    def or2_request_id(self):
        with self.case("OR-2") as observed:
            since = iso_now()
            sent, body = self.transfer(self.flow, self.pn, self.cell_b, self.cell_a, 10, **{"X-Request-ID": "or-2-check"})
            self.flow_area = self.cell_a
            generated, _ = self.transfer(self.flow, self.pn, self.cell_a, self.cell_b, 10)
            self.flow_area = self.cell_b
            check(sent.header("x-request-id") == "or-2-check", f"echoed id {sent.header('x-request-id')}")
            other = generated.header("x-request-id") or ""
            check(re.fullmatch(r"[0-9a-f]{32}", other) is not None, f"generated id {other!r}")
            time.sleep(2)
            web, _ = self.json_lines(self.logs("web", since))
            backend, _ = self.json_lines(self.logs("backend", since))
            for request_id, payload in (("or-2-check", body), (other, None)):
                web_lines = [r for r in web if r.get("request_id") == request_id]
                records = [r for r in backend if r.get("request_id") == request_id and r.get("logger") == "app.access"]
                check(len(web_lines) == 1 and len(records) == 1, f"{request_id}: web {len(web_lines)}, backend {len(records)}")
                check(web_lines[0]["path"].endswith("/transfers") and records[0]["status"] == 201, "record shape")
                if payload is not None:
                    context = records[0]["context"]
                    observed["context"] = context
                    for key in ("station_device_id", "station_id", "part_number", "quantity_flow_id", "quantity",
                                "source_area_id", "target_area_id", "device_event_id", "area_id"):
                        check(key in context, f"context lacks {key}")
                    check(context["device_event_id"] == payload["device_event_id"], "device_event_id differs")
                    check(records[0]["outcome"] == "created", "outcome")
            observed["web_keys"] = list(web_lines[0])

    # -- OR-3 (after OR-14) ---------------------------------------------------

    def or3_secrets(self):
        """Every check of OR-3 is evaluated; the case fails with the list of the ones that did not hold."""
        with self.case("OR-3") as observed:
            problems = []
            backend_lines = self.first_backend_lines + self.logs("backend")
            web_lines = self.logs("web")
            db_lines = self.logs("db")
            backend, backend_other = self.json_lines(backend_lines)
            web, web_other = self.json_lines(web_lines)
            unexpected = [text for text in web_other if not WEB_NON_ACCESS.match(text)]
            observed.update({"backend_lines": len(backend_lines), "backend_non_json": backend_other[:5],
                             "web_lines": len(web_lines), "web_non_json": len(web_other),
                             "web_unexpected_non_json": len(unexpected), "db_lines": len(db_lines)})
            if backend_other:
                problems.append(f"{len(backend_other)} backend lines are not JSON")
            if unexpected:
                problems.append(f"{len(unexpected)} web lines are neither JSON nor nginx/entrypoint lines: {unexpected[:2]}")
            values = [v for k, v in self.secret_values.items() if k != "setup_token" and isinstance(v, str)]
            for key in ("enrollment_codes", "device_tokens", "badges"):
                values += self.secret_values.get(key, [])
                values += [v.replace("-", "") for v in self.secret_values.get(key, []) if "-" in v]
            text = "\n".join(backend_lines + web_lines + db_lines)
            leaked = sorted({i for i, value in enumerate(values) if value and value in text})
            observed["secret_values_checked"] = len(values)
            if leaked:
                problems.append(f"{len(leaked)} secret values appear in the logs")
            if "postgresql://" in text or "postgresql+psycopg://" in text:
                problems.append("a DSN appears in the logs")
            detail = [t for t in db_lines if "DETAIL:" in t]
            observed["db_detail_lines"] = len(detail)
            if detail:
                problems.append("a db line holds DETAIL:")
            token = self.secret_values["setup_token"]
            observed["setup_token_occurrences"] = text.count(token)
            announcement = [r for r in backend if token in json.dumps(r)]
            if text.count(token) != 1 or len(announcement) != 1 or announcement[0].get("logger") != "app.first_run":
                problems.append(f"the setup token appears {text.count(token)} times (one app.first_run announcement expected)")
            traceback_records = [r for r in backend if r.get("request_id") == "or-14-500" and r.get("traceback")]
            access = [r for r in backend if r.get("request_id") == "or-14-500" and r.get("logger") == "app.access"]
            web_500 = [r for r in web if r.get("request_id") == "or-14-500"]
            observed["or14"] = {"traceback_records": [r.get("logger") for r in traceback_records],
                                "access": [r.get("level") for r in access], "web": [r.get("status") for r in web_500]}
            if not (traceback_records and access and access[0].get("level") == "ERROR"):
                problems.append("the or-14-500 traceback and ERROR access records do not share the request id")
            if not web_500:
                problems.append("no web line carries the or-14-500 request id")
            observed["problems"] = problems
            check(not problems, "; ".join(problems))

    # -- OR-4 -----------------------------------------------------------------

    def or4_web_stopped(self):
        with self.case("OR-4") as observed:
            self.compose("stop", "web", timeout=300)
            code, lines = self.check_sh()
            observed["stopped"] = {"exit": code, "containers": self.line(lines, "containers"), "https": self.line(lines, "https")}
            check(code == 1, f"exit {code}")
            check(self.line(lines, "containers") == "FAIL containers web is exited", self.line(lines, "containers"))
            check(self.line(lines, "https").startswith("FAIL https "), self.line(lines, "https"))
            code, lines = self.check_sh()
            observed["repeat"] = {"exit": code, "notes": [t for t in lines if t.startswith("NOTE")]}
            check(code == 0 and any(t.startswith("NOTE already reported at ") for t in lines), "repeat not suppressed")
            self.compose("up", "-d", "web", timeout=300)
            self.wait_container("web")
            self.wait_ready()
            code, lines = self.check_sh()
            observed["restored"] = code
            check(code == 0, f"exit {code} after up -d web: {lines}")
            # A scheduled reconcile's one-off backend container beside the service container.
            reports = self.workdir / "reconcile-or4"
            process = subprocess.Popen(
                ["sh", self.reconcile_sh_path.as_posix(), "--env-file", self.env_file.as_posix(), "--reports-dir", reports.as_posix(),
                 "--records-dir", posix_host_path(self.records), "--rehearsal", "--project", self.project],
                cwd=self.checkout, env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            label = ["--filter", f"label=com.docker.compose.project={self.project}", "--filter",
                     "label=com.docker.compose.service=backend", "--filter", "label=com.docker.compose.oneoff=True"]
            seen = False
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and process.poll() is None:
                if self.run(["docker", "ps", "-q", *label]).stdout.split():
                    seen = True
                    break
                time.sleep(0.5)
            code, lines = self.check_sh("--no-state")
            out, err = process.communicate(timeout=900)
            observed["concurrent"] = {"one_off_seen": seen, "containers": self.line(lines, "containers"),
                                      "restarts": self.line(lines, "restarts"), "reconcile_exit": process.returncode,
                                      "reconcile": out.strip().splitlines()[:1]}
            check(self.line(lines, "containers").startswith("PASS containers"), self.line(lines, "containers"))
            check(self.line(lines, "restarts").startswith("PASS restarts"), self.line(lines, "restarts"))

    # -- OR-5 -----------------------------------------------------------------

    def or5_restart(self):
        with self.case("OR-5") as observed:
            container = self.container("backend")
            before = self.inspect(container)["RestartCount"]
            # uvicorn (PID 1) exits on TERM; the restart policy restarts it (the slim image has no kill binary).
            self.run(["docker", "exec", container, "python", "-c", "import os, signal; os.kill(1, signal.SIGTERM)"],
                     check_rc=False)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and self.inspect(container)["RestartCount"] == before:
                time.sleep(1)
            observed["restart_count"] = [before, self.inspect(container)["RestartCount"]]
            self.wait_container("backend")
            self.wait_ready()
            code, lines = self.check_sh()
            observed["after_restart"] = {"exit": code, "restarts": self.line(lines, "restarts")}
            check(self.line(lines, "restarts").startswith("FAIL restarts backend restarted 1 times"), self.line(lines, "restarts"))
            code, lines = self.check_sh()
            observed["next"] = self.line(lines, "restarts")
            check(self.line(lines, "restarts").startswith("PASS restarts"), self.line(lines, "restarts"))

    # -- OR-6 -----------------------------------------------------------------

    def or6_disk(self):
        with self.case("OR-6") as observed:
            df = self.compose("exec", "-T", "db", "sh", "-c", "df -Pk /var/lib/postgresql/data").stdout.splitlines()[-1].split()
            used, avail = int(df[2]), int(df[3])
            fill_mib = max(1, (avail - (used + avail) * 8 // 100) // 1024)
            observed["before_kib"] = {"used": used, "available": avail, "fill_mib": fill_mib}
            self.compose("exec", "-T", "db", "sh", "-c",
                         f"dd if=/dev/zero of=/var/lib/postgresql/data/or6.fill bs=1M count={fill_mib} 2>/dev/null", timeout=300)
            try:
                code, lines = self.check_sh("--only", "disk_data", "--no-state")
                observed["filled"] = self.line(lines, "disk_data")
                check(self.line(lines, "disk_data").startswith("FAIL disk_data "), self.line(lines, "disk_data"))
            finally:
                self.compose("exec", "-T", "db", "rm", "-f", "/var/lib/postgresql/data/or6.fill", timeout=120)
            code, lines = self.check_sh("--only", "disk_data", "--no-state")
            observed["removed"] = self.line(lines, "disk_data")
            check(self.line(lines, "disk_data").startswith("PASS disk_data "), self.line(lines, "disk_data"))

    # -- OR-7 -----------------------------------------------------------------

    def or7_certificate(self):
        with self.case("OR-7") as observed:
            for days, expected in ((1, "FAIL certificate certificate for localhost expires "), (400, "PASS certificate ")):
                directory = self.workdir / f"or7-{days}"
                directory.mkdir()
                self.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(days),
                          "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                          "-keyout", (directory / "key.pem").as_posix(), "-out", (directory / "certificate.pem").as_posix()],
                         env_extra={"MSYS_NO_PATHCONV": "1"})
                os.chmod(directory / "key.pem", 0o644)
                port = free_port()
                name = f"pf-s6-or7-{days}-{int(time.time()) % 100000}"
                self.or7_containers.append(name)
                self.run(["docker", "run", "-d", "--name", name, "--label", f"partflow.rehearsal={self.project}",
                          "-p", f"127.0.0.1:{port}:443", "-v", f"{directory.as_posix()}:/etc/nginx/or7:ro",
                          "-v", f"{OR7_CONF.as_posix()}:/etc/nginx/conf.d/default.conf:ro", NGINX_IMAGE])
                time.sleep(2)
                code, lines = self.check_sh("--only", "certificate", url=f"https://localhost:{port}")
                observed[f"{days}_days"] = {"exit": code, "line": self.line(lines, "certificate")}
                self.run(["docker", "rm", "-f", name], check_rc=False)
                check(self.line(lines, "certificate").startswith(expected), self.line(lines, "certificate"))
                if days == 1:
                    check(self.line(lines, "certificate").endswith("(within 21 days)"), self.line(lines, "certificate"))

    # -- OR-8 -----------------------------------------------------------------

    def or8_stale(self):
        with self.case("OR-8") as observed:
            stale_dir = self.workdir / "stale-backups"
            stale_dir.mkdir()
            os.chmod(stale_dir, 0o700)
            source = self.backup_dir / self.daily
            stamp = datetime.datetime.strptime(self.daily[:16], "%Y%m%dT%H%M%SZ") - datetime.timedelta(hours=30)
            name = stamp.strftime("%Y%m%dT%H%M%SZ") + "-daily"
            target = stale_dir / name
            shutil.copytree(source, target)
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            shift = datetime.timedelta(hours=30)
            for key in ("dump_started_at", "completed_at"):
                value = datetime.datetime.strptime(manifest[key], "%Y-%m-%dT%H:%M:%SZ") - shift
                manifest[key] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
            manifest["name"] = name
            (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            env_file = self.workdir / "stale.env"
            env_file.write_text("".join(
                f"PARTFLOW_BACKUP_DIR={posix_host_path(stale_dir)}\n" if line.startswith("PARTFLOW_BACKUP_DIR=") else line + "\n"
                for line in self.env_file.read_text(encoding="utf-8").splitlines()), encoding="utf-8", newline="\n")
            rc, report, _ = self.status_run(env_file)
            observed["status"] = {"exit": rc, "findings": [f.get("code") for f in report.get("findings") or []]}
            check(rc == 1 and "backup_stale" in observed["status"]["findings"], "status did not report backup_stale")
            state = self.fresh_state("or8")
            code, lines = self.check_sh("--only", "backup_age", state=state, env_file=env_file)
            observed["check"] = self.line(lines, "backup_age")
            check(self.line(lines, "backup_age").startswith("FAIL backup_age The newest backup"), self.line(lines, "backup_age"))
            code, lines = self.check_sh("--only", "backup_age", "--max-backup-age-hours", "48", state=state, env_file=env_file)
            observed["check_48"] = self.line(lines, "backup_age")
            check(self.line(lines, "backup_age").startswith("PASS backup_age"), self.line(lines, "backup_age"))

    # -- OR-9 -----------------------------------------------------------------

    def log_size(self, service):
        lines = self.logs(service)
        return len(lines), sum(len(text.encode("utf-8")) + 1 for text in lines)

    def or9_volume(self):
        with self.case("OR-9") as observed:
            first, second = self.cell(), self.cell()
            flow, pn = self.release_flow(first)
            before = {s: self.log_size(s) for s in ("backend", "web")}
            started = time.monotonic()
            here, there = first, second
            for index in range(COMMANDS_OR9):
                if index % 10 == 0:
                    self.api("GET", f"/api/scan-stations/{there['station_id']}/context", station=there["station_id"], status=200)
                self.api("POST", f"/api/scan-stations/{there['station_id']}/scans/resolve", {"part_number": pn},
                         station=there["station_id"], status=200)
                self.transfer(flow, pn, here, there, 10)
                here, there = there, here
            seconds = time.monotonic() - started
            after = {s: self.log_size(s) for s in ("backend", "web")}
            per_service = {}
            for service in ("backend", "web"):
                lines = after[service][0] - before[service][0]
                size = after[service][1] - before[service][1]
                per_day = size * 5000 / COMMANDS_OR9
                per_service[service] = {"lines_per_1000_commands": lines, "bytes_per_1000_commands": size,
                                        "days_held_at_5000_commands_per_day": round(50 * 1024 * 1024 / per_day, 1) if per_day else None}
            observed.update({"commands": COMMANDS_OR9, "seconds": round(seconds, 1), "per_service": per_service,
                             "rotation": "json-file 10 MB x 5 per container (S2 x-logging)"})

    # -- OR-10 ----------------------------------------------------------------

    def reconcile_sh(self, reports):
        result = self.run(["sh", self.reconcile_sh_path.as_posix(), "--env-file", self.env_file.as_posix(), "--reports-dir",
                           reports.as_posix(), "--records-dir", posix_host_path(self.records), "--rehearsal", "--project",
                           self.project], timeout=1800, check_rc=False, record_output=True, cwd=self.checkout)
        for path in sorted(reports.glob("*-reconcile.json*")):
            self.save_log(f"{reports.name}-{path.name}", path.read_text(encoding="utf-8", errors="replace"))
        return result.returncode, result.stdout.splitlines()

    def or10_clean(self):
        with self.case("OR-10 (clean)") as observed:
            code, lines = self.reconcile_sh(self.workdir / "reconcile")
            observed.update({"exit": code, "lines": lines})
            check(code == 0 and lines and lines[0].startswith("RECONCILE clean "), f"exit {code}: {lines}")

    def or10_finding(self):
        with self.case("OR-10 (h finding)") as observed:
            self.compose("stop", "backend", timeout=300)
            migrate = parse_report(self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason",
                                                "s6 rehearsal: deliberate (h) finding", release=RELEASES["F"], timeout=300,
                                                check_rc=False).stdout)
            observed["migrate"] = {"result": migrate.get("result"), "error": migrate.get("error")}
            check(migrate.get("result") == "upgraded", f"migrate F: {migrate.get('result')} {migrate.get('error')}")
            self.set_env(RELEASES["F"])
            code, lines = self.reconcile_sh(self.workdir / "reconcile-f")
            observed.update({"exit": code, "lines": lines})
            # F serves from here on (OR-14 and OR-3 follow); the recreated backend container starts a new log, so the
            # A container's log is kept for OR-3.
            self.first_backend_lines += self.logs("backend")
            self.compose("up", "-d", "backend", timeout=300)
            self.serving = RELEASES["F"]
            self.wait_ready()
            check(code == 1 and any(t.startswith("FAIL h ") for t in lines), f"exit {code}: {lines}")

    # -- OR-11 ----------------------------------------------------------------

    def or11_lock(self):
        with self.case("OR-11") as observed:
            user, database = self.env_value("POSTGRES_USER"), self.env_value("POSTGRES_DB")
            # Plain `docker exec` (observed on Docker Desktop: while a `docker compose exec` session of the project is
            # attached, a later `docker compose run` of the project may not start until that session ends).
            holder = subprocess.Popen(
                ["docker", "exec", self.container("db"), "psql", "-U", user, "-d", database, "-c",
                 "BEGIN; LOCK TABLE part_movements IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(300); COMMIT;"],
                env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                held = ("SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation WHERE c.relname ="
                        " 'part_movements' AND l.mode = 'AccessExclusiveLock' AND l.granted")
                deadline = time.monotonic() + 60
                while self.psql(held) != "1":
                    check(time.monotonic() < deadline and holder.poll() is None, "the lock holder did not take the lock")
                    time.sleep(0.5)
                started = time.monotonic()
                rc, report, _ = self.status_run()
                observed["status"] = {"exit": rc, "error": (report.get("error") or {}).get("code"),
                                      "seconds": round(time.monotonic() - started, 1)}
                check(rc == 2 and observed["status"]["error"] == "lock_timeout", f"status {observed['status']}")
                code, lines = self.check_sh("--only", "database", "--no-state")
                observed["check"] = self.line(lines, "database")
                check(self.line(lines, "database").startswith("FAIL database The status query waited more than 5 s"),
                      self.line(lines, "database"))
            finally:
                # End the holder's sleep (its transaction then commits; nothing was changed).
                self.psql("SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE query LIKE '%pg_sleep(300)%'"
                          " AND pid <> pg_backend_pid()")
                holder.communicate(timeout=120)

    # -- OR-13 ----------------------------------------------------------------

    def or13_schema(self):
        with self.case("OR-13") as observed:
            since = iso_now()
            self.psql(f"UPDATE alembic_version SET version_num = '{UNKNOWN_REVISION}'")
            try:
                time.sleep(6)
                code, lines = self.check_sh()
                observed["mismatch"] = {"exit": code, "https": self.line(lines, "https"), "schema": self.line(lines, "schema")}
                check(self.line(lines, "https") == "FAIL https HTTP 503 from /api/health: schema mismatch (PartFlow refuses changes)",
                      self.line(lines, "https"))
                check(self.line(lines, "schema").startswith(f"FAIL schema The database is at revision {UNKNOWN_REVISION} "),
                      self.line(lines, "schema"))
                backend, _ = self.json_lines(self.logs("backend", since))
                noisy = [r for r in backend if r.get("logger") == "app.access" and r.get("route") in ("/api/health", "/api/health/live")
                         and r.get("level") in ("INFO", "WARNING", "ERROR")]
                check(not noisy, f"{len(noisy)} health access records at INFO or above")
            finally:
                self.psql(f"UPDATE alembic_version SET version_num = '{self.code_head}'")
            time.sleep(6)
            self.wait_ready()
            code, lines = self.check_sh()
            observed["restored"] = {"exit": code, "https": self.line(lines, "https"), "schema": self.line(lines, "schema")}
            check(code == 0 and self.line(lines, "https").startswith("PASS https") and self.line(lines, "schema").startswith("PASS schema"),
                  f"after restore: {lines}")

    # -- OR-14 ----------------------------------------------------------------

    def or14_errors(self):
        with self.case("OR-14") as observed:
            self.compose("stop", "db", timeout=300)
            try:
                since = iso_now()
                window_started = time.monotonic()
                answer = self.api("GET", "/api/areas", **{"X-Request-ID": "or-14-500"})
                observed["status"] = answer.status
                check(answer.status == 500, f"GET /api/areas answered {answer.status}")
                for _ in range(30):
                    self.client.request("GET", "/api/health")
                time.sleep(2)
                polls_end = iso_now()
                window_seconds = time.monotonic() - window_started
                time.sleep(1)
                code, lines = self.check_sh()
                observed["check"] = {"exit": code, "errors": self.line(lines, "errors"),
                                     "containers": self.line(lines, "containers"), "https": self.line(lines, "https"),
                                     "database": self.line(lines, "database")}
                check(code == 1, f"exit {code}")
                check("or-14-500" in self.line(lines, "errors") and self.line(lines, "errors").startswith("FAIL errors "),
                      self.line(lines, "errors"))
                check(self.line(lines, "containers") == "FAIL containers db is exited", self.line(lines, "containers"))
                check(self.line(lines, "https").startswith("FAIL https"), self.line(lines, "https"))
                check(self.line(lines, "database").startswith("FAIL database"), self.line(lines, "database"))
                backend, _ = self.json_lines(self.logs("backend", since))
                window, _ = self.json_lines(self.logs("backend", since, polls_end))
                tracebacks = [r for r in backend if r.get("request_id") == "or-14-500" and r.get("traceback")]
                check(tracebacks, "no traceback record of the 500")
                check(all("DETAIL" not in r["traceback"] for r in tracebacks), "the traceback holds DETAIL")
                observed["traceback_exception_lines"] = [r["traceback"].strip().splitlines()[-1] for r in tracebacks]
                probes = [r for r in window if r.get("level") == "ERROR" and r.get("logger") != "app.access"
                          and str(r.get("message", "")).startswith("Database revision read failed")]
                observed["probe_records"] = [{k: r.get(k) for k in ("ts", "logger", "message", "request_id")} for r in probes]
                health_records = [r for r in window if r.get("logger") == "app.access"
                                  and r.get("route") in ("/api/health", "/api/health/live") and r.get("level") != "DEBUG"]
                # DV-2: at most one probe ERROR per worker process per 60 s (the stopped db makes the 500 request slow,
                # so the window may span more than one interval).
                workers = int(self.env_value("PARTFLOW_BACKEND_WORKERS") or 2)
                allowed = workers * (1 + int(window_seconds // 60))
                observed.update({"probe_errors": len(probes), "window_seconds": round(window_seconds, 1),
                                 "allowed_probe_errors": allowed})
                check(len(probes) <= allowed, f"{len(probes)} probe ERROR records in {window_seconds:.0f} s (at most {allowed})")
                check(not health_records, "health access records above DEBUG")
                cursor = (self.monitoring / "errors-since").read_text(encoding="utf-8").strip()
            finally:
                self.compose("up", "-d", "--wait", "db", timeout=300)
            # The rehearsal database volume is a tmpfs (OR-6): stopping db discarded it. Re-install the empty schema of
            # the serving release (roles, migrate) so that the next check reads a ready stack again.
            if not self.psql("SELECT to_regclass('public.alembic_version')"):
                observed["reinstalled_after_db_restart"] = True
                self.compose("stop", "backend", timeout=300)
                self.compose("--profile", "ops", "run", "--rm", "-T", "db-roles", timeout=300)
                migrate = parse_report(self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason",
                                                    "s6 rehearsal: tmpfs volume re-install", timeout=300,
                                                    check_rc=False).stdout)
                check(migrate.get("result") == "upgraded", f"re-install migrate: {migrate.get('result')} {migrate.get('error')}")
                self.compose("up", "-d", "backend", timeout=300)
            self.wait_ready()
            code, lines = self.check_sh()
            observed["after"] = {"exit": code, "errors": self.line(lines, "errors")}
            check(f"since {cursor}" in self.line(lines, "errors"), "the errors window did not start at the stored cursor")
            check("or-14-500" not in self.line(lines, "errors"), "the old window was read again")
            if code != 0:
                code, lines = self.check_sh()
                observed["after_next"] = code

    # -- OR-15, OR-16 ---------------------------------------------------------

    def or15_notification_form(self):
        with self.case("OR-15") as observed:
            before = {name: (self.monitoring / name).read_bytes() for name in ("restarts", "errors-since", "alert-state")
                      if (self.monitoring / name).exists()}
            code, lines = self.check_sh("--only", "disk_backup", "--only", "disk_docker", "--min-free-percent", "100")
            observed.update({"exit": code, "lines": lines})
            check(code == 1 and len(lines) == 2, f"exit {code}: {lines}")
            check(all(t.startswith("FAIL ") for t in lines), str(lines))
            after = {name: (self.monitoring / name).read_bytes() for name in before}
            check(after == before, "state files changed")

    def or16_release_guard(self):
        with self.case("OR-16") as observed:
            lock = self.records / ".release.lock"
            lock.mkdir()
            try:
                code, lines = self.check_sh()
                observed.update({"exit": code, "status_lines": [self.line(lines, i) for i in STATUS_IDS]})
                for check_id in STATUS_IDS:
                    check(self.line(lines, check_id).startswith(f"SKIP {check_id} a release is running ("), self.line(lines, check_id))
                label = ["--filter", f"label=com.docker.compose.project={self.project}", "--filter",
                         "label=com.docker.compose.service=status"]
                check(not self.run(["docker", "ps", "-a", "-q", *label]).stdout.split(), "a status container exists")
                check(len(lines) >= 13, "not every check evaluated")
            finally:
                lock.rmdir()

    # -- run ------------------------------------------------------------------

    def run_cases(self):
        self.or1_install()
        if self.evidence["cases"]["OR-1"]["status"] != "pass":
            raise CaseFailure("OR-1 failed: the later cases need the installed stack")
        self.or2_request_id()
        self.or16_release_guard()
        self.or15_notification_form()
        self.or8_stale()
        self.or7_certificate()
        self.or6_disk()
        self.or11_lock()
        self.or4_web_stopped()
        self.or5_restart()
        self.or13_schema()
        self.or9_volume()
        self.or10_clean()
        self.or10_finding()
        self.or14_errors()
        self.or3_secrets()
        self.evidence["cases"]["OR-12"] = {"status": "not_run", "observed": {
            "reason": "separate runs of stack_smoke.py, release_rehearsal.py and backup_rehearsal.py (recorded by the caller)"}}

    def teardown(self):
        if not self.args.keep:
            for name in self.or7_containers:
                self.run(["docker", "rm", "-f", name], check_rc=False)
        super().teardown()


def main(argv):
    parser = argparse.ArgumentParser(description="P16-S6 observability rehearsal (throwaway Compose project).")
    parser.add_argument("--evidence", required=True, help="path of the evidence JSON to write")
    parser.add_argument("--log-dir", help="directory for the step logs")
    parser.add_argument("--keep", action="store_true", help="leave the stack and images (prints the teardown commands)")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f"Compose project name (default {DEFAULT_PROJECT})")
    args = parser.parse_args(argv)
    evidence = {
        "slice": "P16-S6",
        "project": args.project,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "releases": dict(RELEASES),
        "commands": [],
        "cases": {},
    }
    rehearsal = ObservabilityRehearsal(args, evidence)
    rehearsal.refuse_foreign_project()
    outcome = 1
    try:
        rehearsal.execute()
        statuses = [c["status"] for c in evidence["cases"].values()]
        outcome = 1 if "fail" in statuses or evidence.get("run_errors") or not statuses else 0
    finally:
        evidence["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        evidence["outcome"] = {0: "pass", 1: "fail"}[outcome]
        path = Path(args.evidence)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"evidence: {path} ({evidence['outcome']})", flush=True)
    return outcome


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
