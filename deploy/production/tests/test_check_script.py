"""P16-S6: deploy/production/check.sh against fake `docker`, `curl`, `openssl`, `df`, `date`, `id`, `timeout` and `mv`
executables (P16-S6 SPEC section 6.3, cases CK-1..CK-23, and the audit cases CK-24..CK-26).

The fake-tool harness of test_release_scripts.py is reused through test_backup_scripts.ScriptHarness: a temporary fake
repository root (copies of both production Compose files and an env file whose PARTFLOW_BACKUP_DIR is a temporary
directory), a `bin` directory first on PATH whose fakes append their argv to calls.jsonl and answer from the case's rule
table, and the real `sh`. Here the fake `timeout` logs its arguments and then runs the bounded command (another fake),
unless its rule answers 124 (expiry); the fake `mv` logs and then runs the real `mv`; the fake `date` supplies the clock.
check.sh runs from the repository, so the real monitor_report.py evaluates the fake `status` report under a `python3`
wrapper of this interpreter. Nothing touches a Docker daemon, a database or the network.

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
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backup_scripts import ScriptHarness  # noqa: E402
from test_monitor_report import status_doc  # noqa: E402
from test_release_scripts import FAKE_TOOL, SH_REQUIRED, find_sh, out, rule, sh_path  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CHECK_SH = REPO / "deploy" / "production" / "check.sh"
SYSTEM_STATUS = REPO / "backend" / "app" / "application" / "system_status.py"
RELEASE = "v1.0.0"
HOST = "partflow.example"
URL = f"https://{HOST}"
PROJECT_LABEL = "label=com.docker.compose.project=partflow-production"
SERVICES = ("db", "backend", "web")
CHECK_IDS = ("https", "certificate", "containers", "restarts", "errors", "disk_data", "disk_backup", "disk_docker",
             "disk_archive", "database", "schema", "backup_age", "archival_proposal")
STATUS_RUN = r"--profile ops run --rm --no-deps -T --user 1000:1000 status --max-backup-age-hours (\d+)$"
LOGS = r"logs --no-log-prefix --since (\S+) --until (\S+) backend$"
# Git Bash converts an absolute POSIX argument for a native (Windows) fake: "/var/..." may arrive as "C:/.../var/...".
EXEC_DF = r"exec -T db df -Pk .*/var/lib/postgresql/data$"
DOCKER_ROOT = "/var/lib/docker"
EPOCH = 1791460800  # 2026-10-08T12:00:00Z
STARTED = "2026-10-08T00:00:00.000000000Z"


def iso(epoch):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def df_text(used_kib, available_kib, mount="/"):
    return f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/fake {used_kib + available_kib} {used_kib} {available_kib} 50% {mount}\n"


def health(release=RELEASE, schema="current", accepted=None):
    return json.dumps({"status": "ok", "service": "partflow-api", "database": "connected", "release": release,
                       "commit": "0" * 40, "schema": schema, "expected_revision": "0032_phase14_route_adjusted",
                       "database_revision": "0032_phase14_route_adjusted", "accepted_revision": accepted},
                      separators=(",", ":"))


def http(status, body=""):
    return {"status": status, "body": body, "headers": {"Content-Type": "application/json"}}


def ps_rule(service, ids, oneoff="False"):
    pattern = (rf"^ps -a -q --filter {PROJECT_LABEL} --filter label=com\.docker\.compose\.service={service}"
               rf" --filter label=com\.docker\.compose\.oneoff={oneoff}$")
    return rule(pattern, out("".join(f"{i}\n" for i in ids)))


def inspect_rule(container, status="running", health_status="healthy", count=0, oom="false", started=STARTED):
    return rule(rf"^inspect --format .* {re.escape(container)}$",
                out(f"{container} {status} {health_status} {count} {oom} {started}\n"))


def log_line(level, request_id=None, message="x"):
    record = {"ts": "2026-10-08T11:59:00.000Z", "level": level, "logger": "app.access", "message": message}
    if request_id:
        record["request_id"] = request_id
    return json.dumps(record, separators=(",", ":")) + "\n"


class CheckHarness(ScriptHarness):
    tools = ("docker", "df", "id", "curl", "openssl", "date")

    def setUp(self):
        super().setUp()
        # The real tools print LF line ends; on Windows a Python fake would print CRLF unless told otherwise.
        self.fake.write_text("import sys\nsys.stdout.reconfigure(newline='\\n')\nsys.stderr.reconfigure(newline='\\n')\n"
                             + FAKE_TOOL, encoding="utf-8")
        state = self.state.as_posix()
        fake = self.fake.as_posix()
        # timeout: logged, then the bounded command runs, unless the rule answers 124 (expiry).
        self.wrapper(self.bin / "timeout", f'"{self.python}" "{fake}" "{state}" timeout "$@"\nrc=$?\n'
                                           'if [ "$rc" -eq 124 ]; then exit 124; fi\nshift\nexec "$@"')
        real_mv = shutil.which("mv")
        if real_mv is None:
            raise AssertionError("no mv executable on PATH")
        self.wrapper(self.bin / "mv", f'"{self.python}" "{fake}" "{state}" mv "$@"\nexec "{sh_path(real_mv)}" "$@"')
        self.monitoring = self.tmp / "monitoring"
        self.records_dir = self.tmp / "deployments"
        self.status_document = status_doc()
        self.status_rc = 0
        self.rules.update({
            "docker": self.green_docker(),
            "curl": [rule(r"^GET /api/health$", http(200, health()))],
            "openssl": [
                rule(r"^s_client -connect ", out("-----BEGIN CERTIFICATE-----\nMIIfake\n-----END CERTIFICATE-----\n")),
                rule(r"^x509 -noout -enddate$", out("notAfter=Dec 31 12:00:00 2026 GMT\n")),
                rule(r"^x509 -noout -checkend \d+$", out("Certificate will not expire\n")),
            ],
            "df": [
                rule(r"^-Pk .*backups$", out(df_text(10 * 1048576, 90 * 1048576))),
                rule(r"^-Pk .*" + re.escape(DOCKER_ROOT) + "$", out(df_text(20 * 1048576, 80 * 1048576))),
            ],
            "timeout": [rule(r".*")],
            "mv": [rule(r".*")],
        })
        self.set_clock(EPOCH)

    def green_docker(self):
        rules = [rule(r"config --quiet$")]
        for service in SERVICES:
            rules += [ps_rule(service, [f"cid-{service}"]), inspect_rule(f"cid-{service}")]
        rules += [
            rule(LOGS, out(log_line("INFO", "aaa"))),
            rule(EXEC_DF, out(df_text(30 * 1048576, 70 * 1048576, "/var/lib/postgresql/data"))),
            rule(r"^info --format \{\{\.DockerRootDir\}\}$", out(DOCKER_ROOT + "\n")),
        ]
        return rules

    def set_clock(self, epoch):
        self.rules["date"] = [rule(r"^-u \+%s$", out(f"{epoch}\n")),
                              rule(r"^-u \+%Y-%m-%dT%H:%M:%SZ$", out(iso(epoch) + "\n"))]

    def set_status(self, document, rc):
        self.status_document, self.status_rc = document, rc

    def check(self, *arguments, url=URL, defaults=True):
        self.rules["docker"] = [r for r in self.rules["docker"] if r["match"] != STATUS_RUN] + [
            rule(STATUS_RUN, out(self.status_document, rc=self.status_rc))]
        base = ["--url", url] if url else []
        if defaults:
            base += ["--state-dir", self.monitoring.as_posix(), "--records-dir", self.records_dir.as_posix()]
        return self.run_script(CHECK_SH, *base, *arguments)

    def lines(self):
        return self.result.stdout.splitlines()

    def line(self, check_id):
        found = [text for text in self.lines() if re.match(rf"^(PASS|FAIL|SKIP) {check_id} ", text)]
        self.assertEqual(len(found), 1, f"{check_id}: {self.lines()}\nstderr:\n{self.result.stderr}")
        return found[0]

    def state_bytes(self):
        return {name: (self.monitoring / name).read_bytes() if (self.monitoring / name).exists() else None
                for name in ("restarts", "errors-since", "alert-state")}

    def timeout_calls(self):
        return [e["argv"] for e in self.calls("timeout")]

    def status_runs(self):
        return [c for c in self.docker() if re.search(STATUS_RUN, c)]


# ---------------------------------------------------------------------------
# CK-1..CK-22
# ---------------------------------------------------------------------------


class Check(CheckHarness):
    # CK-1
    def test_ck1_all_green(self):
        self.check()
        self.assertExit(0)
        self.assertEqual([text.split(" ", 2)[1] for text in self.lines()], list(CHECK_IDS))
        for text in self.lines():
            expected = "SKIP" if text.split(" ")[1] in ("disk_archive", "archival_proposal") else "PASS"
            self.assertTrue(text.startswith(expected + " "), text)
        self.assertEqual(self.line("certificate"),
                         f"PASS certificate certificate for {HOST} valid until 2026-12-31 12:00:00 GMT (more than 21 days)")
        self.assertEqual(self.line("containers"), "PASS containers db, backend, web running and healthy")
        self.assertTrue(self.line("database").startswith("PASS database partflow as partflow_app: "))
        # Command order across the fakes (timeout, date, id and mv are bookkeeping).
        order = []
        for entry in self.calls():
            if entry["tool"] in ("docker", "curl", "openssl", "df"):
                text = " ".join(entry["argv"])
                order.append(entry["tool"] + " " + (text.replace("compose -f compose.production.yaml --env-file .env.production ", "")))
        patterns = [
            r"^docker config --quiet$", r"^curl ", r"^openssl s_client -connect partflow\.example:443 -servername partflow\.example$",
            r"^openssl x509 -noout -enddate$", r"^openssl x509 -noout -checkend 1814400$",
            r"^docker ps .*service=db ", r"^docker inspect .* cid-db$", r"^docker ps .*service=backend ", r"^docker inspect .* cid-backend$",
            r"^docker ps .*service=web ", r"^docker inspect .* cid-web$", r"^docker logs --no-log-prefix --since ",
            r"^docker exec -T db df -Pk .*/var/lib/postgresql/data$", r"^df -Pk .*backups$", r"^docker info ",
            r"^df -Pk .*/var/lib/docker$", r"^docker ps .*service=status --filter label=com\.docker\.compose\.oneoff=True$",
            r"^docker --profile ops run --rm --no-deps -T --user 1000:1000 status --max-backup-age-hours 26$",
        ]
        self.assertEqual(len(order), len(patterns), order)
        for text, pattern in zip(order, patterns):
            self.assertRegex(text, pattern)
        # Every docker/openssl/df call is bounded by timeout with the section 3.6 bounds.
        bounded = {" ".join(argv[1:]): argv[0] for argv in self.timeout_calls()}
        for entry in self.calls():
            if entry["tool"] in ("docker", "openssl", "df"):
                command = " ".join([entry["tool"], *entry["argv"]])
                matches = [seconds for text, seconds in bounded.items() if text.endswith(" ".join(entry["argv"]))
                           and text.split(" ")[0] == entry["tool"]]
                self.assertTrue(matches, f"not bounded by timeout: {command}")
                expected = "120" if " status --max-backup-age-hours" in command else ("15" if entry["tool"] == "openssl" else "30")
                self.assertEqual(matches[0], expected, command)
        self.assertIn(["5", "true"], self.timeout_calls())
        self.assertTrue((self.monitoring / "status.json").exists())
        moved = [e["argv"] for e in self.calls("mv")]
        for name in ("status.json", "status.err"):
            target = f"{self.monitoring.as_posix()}/{name}"
            self.assertIn(["-f", f"{target}.tmp", target], moved, name)
        # --quiet prints nothing on a green run.
        self.check("--quiet")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "")

    # CK-2
    def test_ck2_https(self):
        cases = [
            (http(503, json.dumps({"schema": "mismatch", "release": RELEASE}, separators=(",", ":"))), (),
             "FAIL https HTTP 503 from /api/health: schema mismatch (PartFlow refuses changes)"),
            (http(502, '{"detail":"x","server_unavailable":true}'), (), "FAIL https HTTP 502 from web: the backend is not answering"),
            (http(200, health(release="v1.0.1")), (), f"FAIL https running release v1.0.1 differs from .env.production ({RELEASE})"),
            (http(200, health(schema="accepted", accepted="9999_x")), (),
             "FAIL https schema accepted by the rollback override 9999_x (use --allow-accepted-schema while that is intended)"),
            (http(200, health(schema="accepted", accepted="9999_x")), ("--allow-accepted-schema",), "PASS https "),
            ({"rc": 60}, (), "FAIL https TLS certificate not trusted (curl 60)"),
            ({"rc": 7}, (), "FAIL https connection failed (curl 7)"),
            ({"rc": 28}, (), "FAIL https no answer within 15 s (curl 28)"),
        ]
        for answer, extra, expected in cases:
            with self.subTest(expected=expected):
                self.reset()
                self.rules["curl"] = [rule(r"^GET /api/health$", answer)]
                self.check("--only", "https", *extra)
                self.assertTrue(self.line("https").startswith(expected), self.line("https"))
                self.assertExit(0 if expected.startswith("PASS") else 1)

    # CK-3, CK-4
    def test_ck3_ck4_resolve_and_cacert(self):
        cacert = self.tmp / "ca.pem"
        cacert.write_text("fake ca\n", encoding="utf-8")
        self.check("--only", "https", "--only", "certificate", "--resolve-to", "10.0.0.5", "--cacert", cacert.as_posix())
        self.assertExit(0)
        curl = self.calls("curl")[0]["argv"]
        self.assertIn("--resolve", curl)
        self.assertEqual(curl[curl.index("--resolve") + 1], f"{HOST}:443:10.0.0.5")
        self.assertIn("--cacert", curl)
        self.assertEqual(curl[-1], f"{URL}/api/health")
        s_client = [e["argv"] for e in self.calls("openssl") if e["argv"][0] == "s_client"][0]
        self.assertEqual(s_client, ["s_client", "-connect", "10.0.0.5:443", "-servername", HOST])
        for entry in self.calls("openssl"):
            self.assertNotIn("--cacert", entry["argv"])
            self.assertNotIn(cacert.as_posix(), " ".join(entry["argv"]))

    # CK-5
    def test_ck5_http_url_needs_rehearsal(self):
        self.check(url="http://127.0.0.1:18080")
        self.assertExit(2)
        self.assertTrue(self.result.stdout.startswith("ERROR check could not run: --url must be"), self.result.stdout)
        self.assertEqual(self.calls("docker"), [])

    # CK-6
    def test_ck6_certificate(self):
        self.prepend("openssl", rule(r"^x509 -noout -checkend \d+$", out("Certificate will expire\n", rc=1)),
                     rule(r"^x509 -noout -enddate$", out("notAfter=Oct 20 12:00:00 2026 GMT\n")))
        self.check("--only", "certificate")
        self.assertExit(1)
        self.assertEqual(self.line("certificate"),
                         f"FAIL certificate certificate for {HOST} expires 2026-10-20 12:00:00 GMT (within 21 days)")
        self.reset()
        self.prepend("openssl", rule(r"^s_client ", out("", rc=1)), rule(r"^x509 -noout -enddate$", out("", rc=1)))
        self.check("--only", "certificate")
        self.assertEqual(self.line("certificate"), f"FAIL certificate no certificate could be read from {HOST}:443")

    # CK-7
    def test_ck7_containers(self):
        self.rules["docker"] = [r for r in self.rules["docker"] if "cid-" not in json.dumps(r)]
        self.prepend("docker", ps_rule("db", []), ps_rule("backend", ["cid-backend"]), ps_rule("web", ["cid-web"]),
                     inspect_rule("cid-backend", status="exited", health_status="unhealthy"),
                     inspect_rule("cid-web", health_status="unhealthy"))
        self.check()
        self.assertExit(1)
        self.assertEqual(self.line("containers"), "FAIL containers db has no container; backend is exited; web is unhealthy")
        self.assertEqual(self.line("disk_data"), "FAIL disk_data cannot measure: db is not running")
        self.reset()
        self.prepend("docker", inspect_rule("cid-backend", health_status="starting"))
        self.check("--only", "containers")
        self.assertExit(0)
        self.assertEqual(self.line("containers"), "PASS containers db, backend, web running; backend (health: starting)")

    # CK-7b
    def test_ck7b_one_off_containers_are_ignored(self):
        # The one-off reconcile container carries oneoff=True: the label-filtered service query never returns it.
        self.prepend("docker", ps_rule("backend", ["cid-run-1"], oneoff="True"))
        self.check()
        self.assertExit(0)
        self.assertEqual(self.line("containers"), "PASS containers db, backend, web running and healthy")
        self.assertFalse([c for c in self.docker() if "cid-run-1" in c
                          or ("oneoff=True" in c and "service=status" not in c)], self.docker())
        for text in self.docker():
            if text.startswith("ps ") and "service=status" not in text:
                self.assertIn("--filter label=com.docker.compose.oneoff=False", text)
                self.assertNotIn(" ps -a\n", text)
        self.reset()
        self.prepend("docker", ps_rule("backend", ["cid-backend", "cid-backend-2"]))
        self.check("--only", "containers")
        self.assertEqual(self.line("containers"), "FAIL containers backend has 2 containers")

    # CK-8
    def test_ck8_disk_data(self):
        self.prepend("docker", rule(EXEC_DF, out(df_text(86 * 1048576, 14 * 1048576, "/var/lib/postgresql/data"))))
        self.check("--only", "disk_data")
        self.assertExit(1)
        self.assertEqual(self.line("disk_data"),
                         "FAIL disk_data 14 % free (14.0 GiB of 100.0 GiB) on the database volume; threshold 15 %")

    # CK-9
    def test_ck9_backup_and_docker_disks(self):
        self.rules["df"] = [rule(r"^-Pk .*backups$", out(df_text(90 * 1048576, 10 * 1048576))),
                            rule(r"^-Pk .*/var/lib/docker$", out(df_text(95 * 1048576, 5 * 1048576)))]
        self.prepend("docker", rule(EXEC_DF, out("", rc=1, stderr="service \"db\" is not running\n")))
        self.check()
        self.assertExit(1)
        self.assertEqual(self.line("disk_backup"),
                         "FAIL disk_backup 10 % free (10.0 GiB of 100.0 GiB) on the backup directory; threshold 15 %")
        self.assertEqual(self.line("disk_docker"),
                         "FAIL disk_docker 5 % free (5.0 GiB of 100.0 GiB) on the Docker root (images, container logs); threshold 15 %")
        self.assertEqual(self.line("disk_data"), "FAIL disk_data cannot measure: db is not running")

    # CK-10
    def test_ck10_archive(self):
        archive = self.tmp / "archive"
        archive.mkdir()
        self.prepend("df", rule(r"^-Pk .*archive$", out(df_text(90 * 1048576, 10 * 1048576))))
        self.check("--only", "disk_archive", "--archive-dir", sh_path(archive))
        self.assertExit(1)
        self.assertTrue(self.line("disk_archive").startswith("FAIL disk_archive 10 % free"), self.line("disk_archive"))
        self.assertIn("on the archive directory", self.line("disk_archive"))
        self.reset()
        archive = self.tmp / "archive"
        archive.mkdir()
        self.prepend("df", rule(r"^-Pk .*archive$", out(df_text(10 * 1048576, 90 * 1048576))))
        self.check("--only", "disk_archive", "--archive-dir", sh_path(archive))
        self.assertExit(0)
        self.assertTrue(self.line("disk_archive").startswith("PASS disk_archive 90 % free"))
        self.check("--only", "disk_archive")
        self.assertEqual(self.line("disk_archive"), "SKIP disk_archive no archive directory configured (P16-S8)")

    # CK-11
    def test_ck11_restart_baseline(self):
        self.check()
        self.assertExit(0)
        self.assertEqual(self.line("restarts"), "PASS restarts no restart since the last check")
        baseline = (self.monitoring / "restarts").read_text(encoding="utf-8").splitlines()
        self.assertEqual(baseline, [f"{s} cid-{s} 0 {STARTED}" for s in SERVICES])
        # Same id, 0 -> 2.
        self.prepend("docker", inspect_rule("cid-backend", count=2))
        self.check()
        self.assertExit(1)
        self.assertEqual(self.line("restarts"), "FAIL restarts backend restarted 2 times since the last check")
        # Recreated (new id): a new baseline, no failure.
        self.rules["docker"] = self.green_docker()
        self.prepend("docker", ps_rule("backend", ["cid-backend-new"]), inspect_rule("cid-backend-new", count=3))
        self.check()
        self.assertEqual(self.line("restarts"), "PASS restarts no restart since the last check")
        # OOM-killed.
        self.prepend("docker", inspect_rule("cid-backend-new", count=4, oom="true"))
        self.check()
        self.assertEqual(self.line("restarts"),
                         "FAIL restarts backend restarted 1 times since the last check (last exit OOM-killed)")
        # Counter reset by a manual start (baseline 4, count 0, new StartedAt): PASS + NOTE, new baseline.
        restarted = "2026-10-08T11:00:00.000000000Z"
        self.prepend("docker", inspect_rule("cid-backend-new", count=0, started=restarted))
        self.check()
        self.assertEqual(self.line("restarts"), "PASS restarts no restart since the last check")
        self.assertIn(f"NOTE restarts backend counter reset at {restarted}; new baseline", self.lines())
        # Baseline 2, count 1 (reset, then one restart since): FAIL since it was started.
        (self.monitoring / "restarts").write_text(
            f"db cid-db 0 {STARTED}\nbackend cid-backend-new 2 {STARTED}\nweb cid-web 0 {STARTED}\n", encoding="utf-8")
        self.prepend("docker", inspect_rule("cid-backend-new", count=1, started=restarted))
        self.check()
        self.assertEqual(self.line("restarts"),
                         f"FAIL restarts backend restarted 1 times since it was started at {restarted}")

    # CK-12
    def test_ck12_errors_cursor(self):
        lines = log_line("ERROR", "a") + log_line("INFO", "z") + log_line("ERROR", "b") + log_line("CRITICAL", "c")
        self.prepend("docker", rule(LOGS, out(lines)))
        self.check()
        self.assertExit(1)
        first_since = iso(EPOCH - 900)
        self.assertEqual(self.line("errors"),
                         f"FAIL errors 3 error records in the backend log since {first_since} (request ids: a, b, c)")
        logs = [c for c in self.docker() if re.search(LOGS, c)]
        self.assertEqual(re.search(LOGS, logs[-1]).groups(), (first_since, iso(EPOCH)))
        self.assertEqual((self.monitoring / "errors-since").read_text(encoding="utf-8"), iso(EPOCH) + "\n")
        # The next run reads from the stored cursor up to its own T0.
        self.set_clock(EPOCH + 900)
        self.check()
        logs = [c for c in self.docker() if re.search(LOGS, c)]
        self.assertEqual(re.search(LOGS, logs[-1]).groups(), (iso(EPOCH), iso(EPOCH + 900)))
        self.assertEqual((self.monitoring / "errors-since").read_text(encoding="utf-8"), iso(EPOCH + 900) + "\n")
        # A failing logs call: FAIL, the cursor unchanged.
        self.set_clock(EPOCH + 1800)
        self.prepend("docker", rule(LOGS, out("", rc=1, stderr="Error response from daemon\n")))
        self.check()
        self.assertEqual(self.line("errors"), "FAIL errors cannot read the backend log (docker compose logs exit 1)")
        self.assertEqual((self.monitoring / "errors-since").read_text(encoding="utf-8"), iso(EPOCH + 900) + "\n")

    # CK-13
    def test_ck13_backup_stale(self):
        stale = "The newest backup 20261007T020000Z-daily completed 27.1 hours ago; the limit is 26 hours."
        self.set_status(status_doc(1, findings=[("backup_stale", stale)]), 1)
        self.check()
        self.assertExit(1)
        self.assertEqual(self.line("backup_age"), f"FAIL backup_age {stale}")
        self.assertEqual(re.search(STATUS_RUN, self.status_runs()[0]).group(1), "26")
        self.reset()
        self.check("--max-backup-age-hours", "48", "--only", "backup_age")
        self.assertEqual(re.search(STATUS_RUN, self.status_runs()[0]).group(1), "48")

    # CK-14
    def test_ck14_renotification(self):
        failing_df = rule(EXEC_DF, out(df_text(90 * 1048576, 10 * 1048576, "/var/lib/postgresql/data")))
        self.prepend("docker", failing_df)
        self.check()
        self.assertExit(1)
        self.assertIn("failset=disk_data", (self.monitoring / "alert-state").read_text(encoding="utf-8"))
        # Same set 1 h later: suppressed.
        self.set_clock(EPOCH + 3600)
        self.check()
        self.assertExit(0)
        self.assertIn(f"NOTE already reported at {iso(EPOCH)}; next reminder after {iso(EPOCH + 6 * 3600)}", self.lines())
        self.assertIn("FAIL disk_data", self.result.stdout)
        # A changed set: notified at once.
        self.set_clock(EPOCH + 7200)
        self.prepend("docker", rule(LOGS, out(log_line("ERROR", "r1"))))
        self.check()
        self.assertExit(1)
        self.assertIn("failset=disk_data errors", (self.monitoring / "alert-state").read_text(encoding="utf-8"))
        # The same set 7 h later: reminded.
        self.set_clock(EPOCH + 7200 + 7 * 3600)
        self.check()
        self.assertExit(1)
        # All green: exit 0, state cleared.
        self.rules["docker"] = self.green_docker()
        self.set_clock(EPOCH + 7200 + 8 * 3600)
        self.check()
        self.assertExit(0)
        self.assertEqual((self.monitoring / "alert-state").read_text(encoding="utf-8"), "")
        # --renotify-hours 0: every failing run notifies.
        self.prepend("docker", failing_df)
        for minutes in (1, 2):
            self.set_clock(EPOCH + 7200 + 8 * 3600 + minutes * 60)
            self.check("--renotify-hours", "0")
            self.assertExit(1)

    # CK-15
    def test_ck15_quiet(self):
        self.prepend("docker", rule(EXEC_DF, out(df_text(90 * 1048576, 10 * 1048576, "/var/lib/postgresql/data"))))
        self.check("--quiet")
        self.assertExit(1)
        self.assertEqual(len(self.lines()), 13)
        self.set_clock(EPOCH + 60)
        self.check("--quiet")
        self.assertExit(0)
        self.assertEqual(self.result.stdout, "")
        self.assertEqual(self.result.stderr, "")
        self.assertIn("NOTE already reported", (self.monitoring / "last-check.txt").read_text(encoding="utf-8"))

    # CK-16
    def test_ck16_could_not_run(self):
        cases = {
            "no backup dir": ([line for line in self.env_lines if not line.startswith("PARTFLOW_BACKUP_DIR=")], ()),
            "--only bogus": (self.env_lines, ("--only", "bogus")),
            "rehearsal without project": (self.env_lines, ("--rehearsal",)),
            "production project": (self.env_lines, ("--rehearsal", "--project", "partflow-production")),
            "project without rehearsal": (self.env_lines, ("--project", "pf-x")),
        }
        for name, (lines, extra) in cases.items():
            with self.subTest(case=name):
                self.write_env(lines)
                self.check(*extra)
                self.assertExit(2)
                self.assertTrue(self.result.stdout.startswith("ERROR check could not run: "), self.result.stdout)
                self.assertEqual(len(self.lines()), 1)
                self.assertEqual(self.calls("docker"), [])
        with self.subTest(case="docker missing"):
            self.write_env(self.env_lines)
            (self.bin / "docker").unlink()
            path = os.pathsep.join(str(self.bin) for _ in range(1))
            result = subprocess.run([self.sh, CHECK_SH.as_posix(), "--url", URL, "--state-dir", self.monitoring.as_posix()],
                                    cwd=self.root, env={**self.environment(), "PATH": path + os.pathsep + self.minimal_path()},
                                    capture_output=True, text=True, encoding="utf-8", timeout=120)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "ERROR check could not run: docker is not installed.\n")

    def minimal_path(self):
        """The directories of sh's own tools without any docker executable."""
        directories = []
        for tool in ("sed", "grep", "awk", "mktemp", "cut", "tr", "sort", "head", "tail", "wc", "cat", "rm", "chmod",
                     "mkdir", "dirname", "true"):
            found = shutil.which(tool)
            if found and str(Path(found).parent) not in directories:
                directories.append(str(Path(found).parent))
        for directory in directories:
            if (Path(directory) / "docker").exists() or (Path(directory) / "docker.exe").exists():
                self.skipTest(f"a real docker lives beside the shell tools in {directory}")
        return os.pathsep.join(directories)

    # CK-17
    def test_ck17_only_and_no_state(self):
        self.check()
        before = self.state_bytes()
        self.assertTrue(all(v is not None for v in before.values()), before)
        self.reset_calls()
        self.set_clock(EPOCH + 900)
        self.prepend("docker", rule(EXEC_DF, out(df_text(90 * 1048576, 10 * 1048576, "/var/lib/postgresql/data"))))
        self.check("--only", "certificate", "--only", "disk_backup")
        self.assertEqual([text.split(" ")[1] for text in self.lines()], ["certificate", "disk_backup"])
        self.assertFalse(self.status_runs())
        self.assertFalse([c for c in self.docker() if c.startswith("ps ")])
        self.assertEqual(self.state_bytes(), before)
        self.reset_calls()
        self.check("--only", "errors")
        self.assertEqual([text.split(" ")[1] for text in self.lines()], ["errors"])
        logs = [c for c in self.docker() if re.search(LOGS, c)]
        self.assertEqual(re.search(LOGS, logs[0]).group(1), iso(EPOCH))
        self.assertEqual(self.state_bytes(), before)
        # --no-state full run with a failing errors check and a restart increase: all lines, exit 1, no suppression.
        self.prepend("docker", rule(LOGS, out(log_line("ERROR", "e1"))), inspect_rule("cid-web", count=1))
        for _ in range(2):
            self.check("--no-state")
            self.assertExit(1)
            self.assertEqual(len([t for t in self.lines() if not t.startswith("NOTE")]), 13)
            self.assertTrue(self.line("errors").startswith("FAIL errors 1 error records"))
            self.assertTrue(self.line("restarts").startswith("FAIL restarts web restarted 1 times"))
            self.assertEqual(self.state_bytes(), before)

    def reset_calls(self):
        calls = self.state / "calls.jsonl"
        if calls.exists():
            calls.unlink()

    # CK-18
    def test_ck18_default_backup_age_matches_the_application(self):
        source = SYSTEM_STATUS.read_text(encoding="utf-8")
        match = re.search(r"^DEFAULT_MAX_BACKUP_AGE_HOURS\b[^=\n]*=\s*(\d+)\s*$", source, re.M)
        self.assertIsNotNone(match, f"no DEFAULT_MAX_BACKUP_AGE_HOURS literal in {SYSTEM_STATUS}")
        script = CHECK_SH.read_text(encoding="utf-8")
        default = re.search(r"^MAX_AGE=(\d+)$", script, re.M)
        self.assertIsNotNone(default)
        self.assertEqual(default.group(1), match.group(1))
        self.assertEqual(match.group(1), "26")

    # CK-19
    def test_ck19_state_files(self):
        backup_before = sorted(p.name for p in self.backup_dir.rglob("*"))
        self.check(defaults=False, url=URL)
        self.assertExit(0)
        state = self.tmp / "partflow-monitoring"
        self.assertTrue(state.is_dir())
        if os.name != "nt":
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            self.assertEqual((state / "last-check.txt").stat().st_mode & 0o777, 0o600)
        moved = [e["argv"] for e in self.calls("mv")]
        for name in ("alert-state", "restarts", "errors-since", "last-check.txt"):
            self.assertIn(["-f", f"{state.as_posix()}/{name}.tmp", f"{state.as_posix()}/{name}"], moved, name)
        last = (state / "last-check.txt").read_text(encoding="utf-8").splitlines()
        self.assertRegex(last[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z exit 0$")
        self.assertEqual(len(last), 14)
        self.assertEqual(sorted(p.name for p in self.backup_dir.rglob("*")), backup_before)
        # An exit-2 run writes none of the three state files.
        before = {name: (state / name).read_bytes() for name in ("restarts", "errors-since", "alert-state")}
        self.rules["docker"][0:0] = [rule(r"config --quiet$", out("", rc=1, stderr="invalid compose file\n"))]
        self.check(defaults=False, url=URL)
        self.assertExit(2)
        self.assertEqual({name: (state / name).read_bytes() for name in before}, before)
        self.assertIn("exit 2", (state / "last-check.txt").read_text(encoding="utf-8"))

    # CK-20
    def test_ck20_min_free_100(self):
        self.check("--min-free-percent", "100")
        self.assertExit(1)
        for check_id in ("disk_data", "disk_backup", "disk_docker"):
            self.assertTrue(self.line(check_id).startswith(f"FAIL {check_id} "), self.line(check_id))

    # CK-21
    def test_ck21_time_bounds(self):
        self.prepend("timeout", rule(r"^30 docker compose .* exec -T db df ", out(rc=124)))
        self.check("--only", "disk_data")
        self.assertExit(1)
        self.assertEqual(self.line("disk_data"), "FAIL disk_data no answer within 30 s")
        self.reset()
        self.check()
        cursor = (self.monitoring / "errors-since").read_bytes()
        self.set_clock(EPOCH + 900)
        self.prepend("timeout", rule(r"^30 docker compose .* logs ", out(rc=124)))
        self.check()
        self.assertEqual(self.line("errors"), "FAIL errors no answer within 30 s")
        self.assertEqual((self.monitoring / "errors-since").read_bytes(), cursor)
        self.reset()
        self.prepend("timeout", rule(r"^120 docker compose .* status ", out(rc=124)))
        # Before the run another (overlapping) run's status container exists; after it, this run's leftover too.
        self.prepend("docker", rule(r"^ps -a -q --filter " + PROJECT_LABEL + r" --filter label=com\.docker\.compose\.service=status"
                                    r" --filter label=com\.docker\.compose\.oneoff=True$",
                                    out("other-run-1\n"), out("other-run-1\nleftover-1\n")),
                     rule(r"^rm -f leftover-1$"))
        self.check()
        self.assertExit(1)
        self.assertEqual(self.line("database"), "FAIL database no answer within 120 s")
        for check_id in ("schema", "backup_age", "archival_proposal"):
            self.assertEqual(self.line(check_id), f"SKIP {check_id} status did not run")
        removed = [c for c in self.docker() if c.startswith("rm ")]
        self.assertEqual(removed, ["rm -f leftover-1"])
        self.reset()
        self.prepend("timeout", rule(r"^30 docker compose .* config --quiet$", out(rc=124)))
        self.check()
        self.assertExit(2)
        self.assertIn("gave no answer within 30 s", self.result.stdout)

    # CK-22
    def test_ck22_release_guard(self):
        lock = self.records_dir / ".release.lock"
        lock.mkdir(parents=True)
        os.utime(lock, (EPOCH - 60, EPOCH - 60))
        self.check()
        self.assertExit(0)
        for check_id in ("database", "schema", "backup_age", "archival_proposal"):
            self.assertEqual(self.line(check_id), f"SKIP {check_id} a release is running ({self.records_dir.as_posix()}/.release.lock)")
        self.assertFalse(self.status_runs())
        self.assertEqual(len(self.lines()), 13)
        lock.rmdir()
        self.reset_calls()
        self.write_lock("by=release.sh", "release=/srv/records/20261008T100000Z-v1.1.0")
        os.utime(self.lock, (EPOCH - 60, EPOCH - 60))
        self.check()
        self.assertTrue(self.line("database").startswith("SKIP database a release is running ("), self.line("database"))
        self.assertFalse(self.status_runs())
        self.reset_calls()
        self.write_lock("by=backup.sh", "name=20261008T020000Z-daily")
        self.check()
        self.assertTrue(self.line("database").startswith("PASS database "))
        self.assertEqual(len(self.status_runs()), 1)

    # CK-24 (audit F7)
    def test_ck24_stale_release_lock_fails(self):
        lock = self.records_dir / ".release.lock"
        lock.mkdir(parents=True)
        os.utime(lock, (EPOCH - 4 * 3600, EPOCH - 4 * 3600))
        self.check()
        self.assertExit(1)
        self.assertEqual(self.line("database"),
                         f"FAIL database the release lock {lock.as_posix()} is older than 4 h; remove it if no release.sh"
                         " runs (OPERATIONS_RUNBOOK §3)")
        for check_id in ("schema", "backup_age", "archival_proposal"):
            self.assertEqual(self.line(check_id), f"SKIP {check_id} status did not run (release lock {lock.as_posix()})")
        self.assertFalse(self.status_runs())
        # A lock just under the bound is a running release.
        os.utime(lock, (EPOCH - 4 * 3600 + 60, EPOCH - 4 * 3600 + 60))
        self.check("--only", "database")
        self.assertExit(0)
        self.assertTrue(self.line("database").startswith("SKIP database a release is running ("), self.line("database"))
        lock.rmdir()
        # The backup lock that release.sh left behind.
        self.write_lock("by=release.sh", "release=/srv/records/20261008T010000Z-v1.1.0")
        os.utime(self.lock, (EPOCH - 6 * 3600, EPOCH - 6 * 3600))
        self.check("--only", "database", "--only", "backup_age")
        self.assertExit(1)
        # The backup directory arrives in the shell's own path form (sh_path).
        self.assertRegex(self.line("database"), r"^FAIL database the release lock \S+/backups/\.backup\.lock is older than 4 h; ")
        self.assertRegex(self.line("backup_age"), r"^SKIP backup_age status did not run \(release lock \S+/\.backup\.lock\)$")
        self.assertFalse(self.status_runs())

    # CK-25 (audit F4)
    def test_ck25_overlapping_run_never_mixes_the_status_report(self):
        shared = (self.monitoring / "status.json").as_posix()
        # While this run's status runs, another run truncates the shared state copy and starts writing its own.
        overlap = (f"sys.stdout.write({self.status_document!r}); sys.stdout.flush(); "
                   f"open({shared!r}, 'w', encoding='utf-8').write('{{\"overlap')")
        # (Not the STATUS_RUN pattern itself: check() replaces that rule with the plain answer.)
        self.prepend("docker", rule(".*" + STATUS_RUN, {"stdout": "", "rc": 0, "stderr": "", "run": overlap}))
        self.check("--no-state")
        self.assertExit(0)
        self.assertTrue(self.line("database").startswith("PASS database "), self.line("database"))
        self.assertEqual((self.monitoring / "status.json").read_text(encoding="utf-8"), self.status_document)

    # CK-26 (audit F3)
    def test_ck26_recreated_backend_is_noted(self):
        self.check()
        self.assertExit(0)
        self.assertFalse([t for t in self.lines() if t.startswith("NOTE errors")], self.lines())
        recreated = "2026-10-08T12:05:00.000000000Z"
        self.set_clock(EPOCH + 900)
        self.prepend("docker", ps_rule("backend", ["cid-backend-new"]), inspect_rule("cid-backend-new", started=recreated))
        self.check()
        self.assertExit(0)
        self.assertEqual(self.line("errors"), f"PASS errors no error records in the backend log since {iso(EPOCH)}")
        self.assertIn(f"NOTE errors backend was recreated (started at {recreated}): records the replaced container wrote"
                      f" after {iso(EPOCH)} could not be read", self.lines())
        # The baseline follows the new container: the next run notes nothing; --only errors reads the same facts.
        self.set_clock(EPOCH + 1800)
        self.check()
        self.assertFalse([t for t in self.lines() if t.startswith("NOTE errors")], self.lines())
        (self.monitoring / "restarts").write_text(f"backend cid-backend 0 {STARTED}\n", encoding="utf-8")
        self.check("--only", "errors")
        self.assertIn(f"NOTE errors backend was recreated (started at {recreated}): records the replaced container wrote"
                      f" after {iso(EPOCH + 1800)} could not be read", self.lines())

    def write_lock(self, *lines):
        self.lock.mkdir(exist_ok=True)
        text = "\n".join(["host=ck-host", "pid=4242", "started_at=2026-10-08T10:00:00Z", *lines]) + "\n"
        (self.lock / "owner").write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# CK-23 (static)
# ---------------------------------------------------------------------------


class Static(unittest.TestCase):
    def test_ck23_posix_sh_with_help(self):
        text = CHECK_SH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("\nset -eu\n", text)
        self.assertNotIn("\r", text)
        sh = find_sh()
        if sh is None:
            raise AssertionError(SH_REQUIRED)
        result = subprocess.run([sh, CHECK_SH.as_posix(), "--help"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: deploy/production/check.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
