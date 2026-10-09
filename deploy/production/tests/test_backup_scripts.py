"""P16-S5: deploy/production/backup.sh and restore-test.sh against fake `docker`, `df`, `ps`, `uname`, `id`, `curl` and
`sleep` executables (P16-S5 SPEC section 6.3: SB-1, SB-2, BS-1..BS-17, RT-1..RT-19).

The harness of test_release_scripts.py is reused: a temporary fake repository root (a copy of compose.production.yaml,
compose.production.build.yaml and an env file whose PARTFLOW_BACKUP_DIR is a temporary directory), a `bin` directory
placed first on PATH whose fakes append their argv to calls.jsonl and answer from the case's rule table, and the real
`sh`. An answer may also run a few lines of Python (the fake `backup-manifest` publishes the backup directory the way
the real command does) or read stdin (the restore is fed the dump). restore-test.sh runs from a copy in the fake root
beside a STUB smoke.sh (smoke.sh has its own tests); reconcile_regression.py is the real one, run by a `python3`
wrapper of this interpreter. RT-11 resolves the generated override with the real `docker compose config` (no daemon
state is read or changed). Nothing touches a Docker daemon, a database or the network.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_release_scripts import (  # noqa: E402
    FAKE_TOOL, HOST_VARIABLE_PREFIXES, SH_REQUIRED, find_sh, health_body, out, rule, sh_path,
)

REPO = Path(__file__).resolve().parents[3]
BACKUP_SH = REPO / "deploy" / "production" / "backup.sh"
RESTORE_SH = REPO / "deploy" / "production" / "restore-test.sh"
BACKUPS_MODULE = REPO / "backend" / "app" / "application" / "backups.py"
RUNBOOK = REPO / "docs" / "deployment" / "OPERATIONS_RUNBOOK.md"

RELEASE = "v1.0.0"
TOOLS = "v1.1.0"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
IMAGE_BACKEND = "sha256:" + "b" * 64
IMAGE_WEB = "sha256:" + "c" * 64
IMAGE_DB = "sha256:" + "d" * 64
REVISION = "0032_phase14_route_adjusted"
COMPOSE_PREFIX = "compose -f compose.production.yaml --env-file .env.production "
NAME_RE = r"[0-9]{8}T[0-9]{6}Z-(daily|manual|pre-release)(-[A-Za-z0-9][A-Za-z0-9._-]{0,63})?"
DB_SIZE = r"exec -T db sh -c psql .*pg_database_size"
PG_STAT = r"exec -T db sh -c psql .*pg_stat_activity"
PG_DUMP = r"exec -T -e TZ=UTC db sh -c pg_dump "
PG_LIST = r"exec -T -e TZ=UTC db pg_restore --list$"
PG_REVISION = r"exec -T -e TZ=UTC db pg_restore --data-only --table=alembic_version --file=-$"
TOOL_RUN = r"--profile ops run --rm --no-deps -T --user 1000:1000 backup-tools "
LIST_TEXT = (
    ";\n; Archive created at 2026-10-08 02:00:01 UTC\n;     dbname: partflow\n;     TOC Entries: 255\n"
    ";     Format: CUSTOM\n;     Dumped from database version: 16.14\n;\n"
    "4000; 0 16390 TABLE DATA public alembic_version partflow_owner\n"
)
REVISION_SQL = "COPY public.alembic_version (version_num) FROM stdin;\n" + REVISION + "\n\\.\n"

# The fake backup-manifest: assemble <dir>/.partial/<NAME>.publish from the partial files, publish it with one rename,
# remove the partial directory (as the real command does), and print its report.
PUBLISH = '''
import shutil
bd = {bd!r}
name = args[args.index("--name") + 1]
src = os.path.join(bd, ".partial", name)
work = os.path.join(bd, ".partial", name + ".publish")
os.makedirs(work)
for f in ("partflow.dump", "partflow.dump.list"):
    shutil.copyfile(os.path.join(src, f), os.path.join(work, f))
for f, text in (("manifest.json", "{{}}\\n"), ("SHA256SUMS", "x\\n")):
    with open(os.path.join(work, f), "w") as handle:
        handle.write(text)
os.rename(work, os.path.join(bd, name))
shutil.rmtree(src)
'''


def report(command, result, exit_code, **fields):
    return json.dumps({"report_version": 1, "command": command, "result": result, "exit_code": exit_code, **fields},
                      indent=2, ensure_ascii=True) + "\n"


def df_answer(available_kib):
    return out(f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/fake 999999999 1 {available_kib} 1% /\n")


class ScriptHarness(unittest.TestCase):
    """The common fake root of the backup.sh (BS) and restore-test.sh (RT) cases."""

    tools = ("docker", "df", "ps", "uname", "id", "curl", "sleep")

    def setUp(self):
        self.sh = find_sh()
        if self.sh is None:
            raise AssertionError(SH_REQUIRED)
        self._tmp = tempfile.TemporaryDirectory(prefix="pf-s5-scripts-")
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "checkout"
        self.root.mkdir()
        for name in ("compose.production.yaml", "compose.production.build.yaml"):
            shutil.copyfile(REPO / name, self.root / name)
        self.backup_dir = self.tmp / "backups"
        self.backup_dir.mkdir()
        os.chmod(self.backup_dir, 0o700)
        self.lock = self.backup_dir / ".backup.lock"
        self.records = self.tmp / "records"
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.fake = self.tmp / "fake_tool.py"
        self.fake.write_text(FAKE_TOOL, encoding="utf-8")
        self.python = Path(sys.executable).as_posix()
        state = self.state.as_posix()
        for tool in self.tools:
            self.wrapper(self.bin / tool, f'"{self.python}" "{self.fake.as_posix()}" "{state}" {tool} "$@"\nrc=$?\n'
                                          f'if [ -f "{state}/signal" ]; then\n'
                                          f'    signal=$(cat "{state}/signal"); rm -f "{state}/signal"; kill -s "$signal" "$PPID"\n'
                                          f'fi\nexit $rc')
        self.wrapper(self.bin / "python3", f'exec "{self.python}" "$@"')
        self.env_file = self.root / ".env.production"
        self.env_lines = [
            f"PARTFLOW_RELEASE={RELEASE}",
            "PARTFLOW_SECRETS_DIR=/srv/partflow/secrets",
            f"PARTFLOW_BACKUP_DIR={sh_path(self.backup_dir)}",
            "PARTFLOW_SITE_TIMEZONE=UTC",
            "PARTFLOW_HTTP_PORT=18080",
            "POSTGRES_USER=partflow_owner",
            "POSTGRES_DB=partflow",
            "PARTFLOW_EDGE_SUBNET=172.30.250.0/24",
            "PARTFLOW_DB_MEMORY=1g",
        ]
        self.write_env(self.env_lines)
        self.rules = {
            "df": [rule(r".*", df_answer(50 * 1024 * 1024))],
            "ps": [rule(r".*")],
            "uname": [rule(r"^-n$", out("bs-host\n"))],
            "id": [rule(r"^-u$", out("1000\n")), rule(r"^-g$", out("1000\n"))],
            "sleep": [rule(r".*")],
            "docker": [],
            "curl": [],
        }
        self.watch([self.lock / "owner"])

    def tearDown(self):
        self._tmp.cleanup()

    def reset(self):
        self.tearDown()
        self.setUp()

    @staticmethod
    def wrapper(path, line):
        path.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8", newline="\n")
        os.chmod(path, 0o755)

    def write_env(self, lines):
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def watch(self, paths):
        (self.state / "watch.json").write_text(json.dumps([str(p) for p in paths]), encoding="utf-8")

    def prepend(self, tool, *rules):
        self.rules[tool][0:0] = list(rules)

    def environment(self):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(HOST_VARIABLE_PREFIXES)}
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["HOME"] = self.tmp.as_posix()
        return env

    def run_script(self, script, *arguments):
        (self.state / "rules.json").write_text(json.dumps(self.rules), encoding="utf-8")
        self.result = subprocess.run(
            [self.sh, Path(script).as_posix(), *arguments], cwd=self.root, env=self.environment(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        return self.result

    def calls(self, tool=None):
        path = self.state / "calls.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [e for e in entries if tool is None or e["tool"] == tool]

    def docker(self):
        labels = []
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            labels.append(text[len(COMPOSE_PREFIX):] if text.startswith(COMPOSE_PREFIX) else text)
        return labels

    def docker_entries(self, pattern):
        return [e for e in self.calls("docker") if re.search(pattern, " ".join(e["argv"]))]

    def assertExit(self, code):
        self.assertEqual(self.result.returncode, code, f"stdout:\n{self.result.stdout}\nstderr:\n{self.result.stderr}")

    def assertNoDocker(self, *patterns):
        for pattern in patterns:
            self.assertFalse([c for c in self.docker() if re.search(pattern, c)], f"unexpected docker {pattern}: {self.docker()}")


# ---------------------------------------------------------------------------
# SB-1, SB-2 (static)
# ---------------------------------------------------------------------------


class Static(unittest.TestCase):
    # SB-1: backup.sh's pg_dump options are backups.DUMP_OPTIONS (read from the Python source, not imported).
    def test_sb1_dump_options(self):
        source = BACKUPS_MODULE.read_text(encoding="utf-8")
        match = re.search(r"^DUMP_OPTIONS\b[^=\n]*=\s*[\[(](.*?)[\])]", source, re.M | re.S)
        self.assertIsNotNone(match, f"no DUMP_OPTIONS literal in {BACKUPS_MODULE}")
        options = re.findall(r'"([^"]+)"', match.group(1))
        lines = [line for line in BACKUP_SH.read_text(encoding="utf-8").splitlines() if "pg_dump -U" in line]
        self.assertEqual(len(lines), 1, lines)
        arguments = re.search(r'-d "\$POSTGRES_DB" (.*?)\'', lines[0]).group(1).split()
        self.assertEqual(arguments, options)
        self.assertEqual(options, ["--format=custom", "--no-owner", "--no-privileges", "--lock-wait-timeout=60s"])

    # SB-2: TZ=UTC on every dump/list exec (the archive stores local-time fields); every restore is one transaction.
    def test_sb2_time_zone_and_single_transaction(self):
        text = BACKUP_SH.read_text(encoding="utf-8")
        execs = [line for line in text.splitlines() if re.search(r"\bexec\b.*(sh -c 'pg_dump|pg_restore)", line)]
        self.assertEqual(len(execs), 3, execs)
        for line in execs:
            self.assertIn("exec -T -e TZ=UTC db", line)
        restores = [line for line in RESTORE_SH.read_text(encoding="utf-8").splitlines()
                    if re.search(r"pg_restore\b.*\s-d\s", line)]
        self.assertEqual(len(restores), 1, restores)
        for line in restores:
            self.assertIn("--single-transaction --exit-on-error --no-owner --no-privileges", line)
        runbook = RUNBOOK.read_text(encoding="utf-8")
        if "--template=template0" not in runbook:
            self.skipTest("OPERATIONS_RUNBOOK.md has no rollback path-3 block yet (P16-S5 DOCS package)")
        for line in runbook.splitlines():
            if re.search(r"pg_restore\b.*\s-d\s", line):
                self.assertIn("--single-transaction", line, line)

    def test_scripts_are_posix_sh_with_help(self):
        for script, usage in ((BACKUP_SH, "Usage: deploy/production/backup.sh"),
                              (RESTORE_SH, "Usage: deploy/production/restore-test.sh")):
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("#!/bin/sh\n"))
                self.assertIn("\nset -eu\n", text)
                self.assertNotIn("\r", text)
                sh = find_sh()
                if sh is None:
                    raise AssertionError(SH_REQUIRED)
                result = subprocess.run([sh, script.as_posix(), "--help"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(usage, result.stdout)


# ---------------------------------------------------------------------------
# backup.sh (BS-1..BS-17)
# ---------------------------------------------------------------------------


class Backup(ScriptHarness):
    def setUp(self):
        super().setUp()
        bd = str(self.backup_dir)
        self.rules["docker"] = [
            rule(r"config --quiet$"),
            rule(r"ps --status running -q db$", out("cid-db\n")),
            rule(r"^image inspect --format \{\{\.Id\}\} partflow/backend:", out(IMAGE_BACKEND + "\n")),
            rule(r"^image inspect --format \{\{\.Id\}\} partflow/web:", out(IMAGE_WEB + "\n")),
            rule(r"^image inspect --format \{\{index \.Config\.Labels \"org\.opencontainers\.image\.revision\"\}\} partflow/backend:",
                 out(COMMIT + "\n")),
            rule(r"^inspect --format \{\{\.Image\}\} cid-db$", out(IMAGE_DB + "\n")),
            rule(r"run --rm --no-deps -T backend python -m app\.cli revision$",
                 out(json.dumps({"report_version": 1, "command": "revision", "state": "current", "exit_code": 0,
                                 "expected_revision": REVISION, "database_revision": REVISION}, indent=2) + "\n")),
            rule(DB_SIZE, out("1048576\n")),
            rule(PG_STAT, out("0\n")),
            rule(PG_DUMP, out("PGDMP fake custom-format dump\n")),
            rule(PG_LIST, out(LIST_TEXT)),
            rule(PG_REVISION, out(REVISION_SQL)),
            rule(TOOL_RUN + "backup-manifest ", {**out(report("backup-manifest", "published", 0, warnings=[])),
                                                 "run": PUBLISH.format(bd=bd)}),
            rule(TOOL_RUN + "backup-verify ", out(report("backup-verify", "verified", 0))),
            rule(TOOL_RUN + "backup-rotate ", out(report("backup-rotate", "rotated", 0))),
        ]

    def backup(self, *arguments, kind="daily", keep=("--keep-daily", "14", "--keep-weekly", "8")):
        base = ["--kind", kind, "--operator", "scheduler"]
        if kind == "daily" and keep:
            base += list(keep)
        return self.run_script(BACKUP_SH, *base, *arguments)

    def name(self):
        match = re.fullmatch(r"BACKUP (" + NAME_RE + r") (\S+)\n", self.result.stdout)
        self.assertIsNotNone(match, self.result.stdout + self.result.stderr)
        return match.group(1), match.group(4)

    def partial_entries(self):
        partial = self.backup_dir / ".partial"
        return sorted(p.name for p in partial.iterdir()) if partial.exists() else []

    def published(self):
        return sorted(p.name for p in self.backup_dir.iterdir() if re.fullmatch(NAME_RE, p.name))

    def write_lock(self, host="bs-host", pid="4242", by="backup.sh", extra=("name=20261008T020000Z-daily",)):
        self.lock.mkdir()
        text = "\n".join([f"host={host}", f"pid={pid}", "started_at=2026-10-08T02:00:00Z", f"by={by}", *extra]) + "\n"
        (self.lock / "owner").write_text(text, encoding="utf-8", newline="\n")
        return text

    # BS-1
    def test_bs1_daily_all_green(self):
        self.backup()
        self.assertExit(0)
        name, path = self.name()
        self.assertRegex(name, r"-daily$")
        self.assertEqual(path, f"{sh_path(self.backup_dir)}/{name}")
        expected = [
            "config --quiet",
            "ps --status running -q db",
            f"image inspect --format {{{{.Id}}}} partflow/backend:{RELEASE}",
            f'image inspect --format {{{{index .Config.Labels "org.opencontainers.image.revision"}}}} partflow/backend:{RELEASE}',
            f"image inspect --format {{{{.Id}}}} partflow/backend:{RELEASE}",
            f"image inspect --format {{{{.Id}}}} partflow/web:{RELEASE}",
            "inspect --format {{.Image}} cid-db",
            "run --rm --no-deps -T backend python -m app.cli revision",
            'exec -T db sh -c psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_database_size(current_database())"',
            'exec -T -e TZ=UTC db sh -c pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner'
            ' --no-privileges --lock-wait-timeout=60s',
            "exec -T -e TZ=UTC db pg_restore --list",
            "exec -T -e TZ=UTC db pg_restore --data-only --table=alembic_version --file=-",
            "--profile ops run --rm --no-deps -T --user 1000:1000 backup-tools backup-manifest --name " + name
            + " --operator scheduler --reason scheduled daily backup --release-tag " + RELEASE + " --release-commit "
            + COMMIT + " --expected-revision " + REVISION + " --environment production --host bs-host --image-backend "
            + IMAGE_BACKEND + " --image-web " + IMAGE_WEB + " --image-db " + IMAGE_DB,
            "--profile ops run --rm --no-deps -T --user 1000:1000 backup-tools backup-verify " + name,
            "--profile ops run --rm --no-deps -T --user 1000:1000 backup-tools backup-rotate --keep-daily 14 --keep-weekly 8",
        ]
        self.assertEqual(self.docker(), expected)
        for entry in self.docker_entries(r"backup-tools "):
            self.assertEqual(entry["env"].get("PARTFLOW_RELEASE"), RELEASE)
        for entry in self.docker_entries(r"exec |run --rm --no-deps -T backend|config"):
            self.assertNotIn("PARTFLOW_RELEASE", entry["env"])
        owner = self.docker_entries(r"backup-manifest ")[0]["watch"][str(self.lock / "owner")]
        self.assertIn("by=backup.sh", owner.splitlines())
        self.assertIn(f"name={name}", owner.splitlines())
        self.assertIn("host=bs-host", owner.splitlines())
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.published(), [name])
        self.assertEqual(self.partial_entries(), [])
        self.assertEqual((self.backup_dir / name / "partflow.dump").read_text(encoding="utf-8"), "PGDMP fake custom-format dump\n")
        self.assertIn("backup: dump ok (", self.result.stderr)
        self.assertIn('"command": "backup-manifest"', self.result.stderr)
        # The second backup sizes its space need from the newest published dump, not the database (the first one is
        # renamed to an older stamp: two backups started in the same second share a name, and the second is refused).
        (self.backup_dir / name).rename(self.backup_dir / "20261007T020000Z-daily")
        self.backup()
        self.assertExit(0)
        self.assertEqual(len(self.docker_entries(DB_SIZE)), 1)

    # BS-1b
    def test_bs1b_retention_options(self):
        for arguments in ((), ("--kind", "manual", "--reason", "x", "--keep-daily", "3"),
                          ("--kind", "daily", "--keep-daily", "14"), ("--kind", "daily", "--no-rotate", "--keep-weekly", "2"),
                          ("--kind", "daily", "--keep-daily", "0", "--keep-weekly", "8")):
            with self.subTest(arguments=arguments):
                self.run_script(BACKUP_SH, "--operator", "x", *(arguments or ("--kind", "daily")))
                self.assertExit(2)
                self.assertEqual(self.calls(), [])
                self.assertEqual(list(self.backup_dir.iterdir()), [])
        with self.subTest(case="--no-rotate"):
            self.run_script(BACKUP_SH, "--kind", "daily", "--operator", "x", "--no-rotate")
            self.assertExit(0)
            self.assertNoDocker(r"backup-rotate")

    # BS-2
    def test_bs2_lock_held_on_another_host(self):
        owner = self.write_lock(host="nas02")
        self.backup()
        self.assertExit(1)
        self.assertIn("backup: refused — a backup is running (backup.sh on nas02, pid 4242, since 2026-10-08T02:00:00Z)."
                      " Nothing was written.", self.result.stderr)
        self.assertNoDocker(r"sh -c pg_dump", PG_STAT)
        self.assertEqual((self.lock / "owner").read_text(encoding="utf-8"), owner)
        self.assertEqual(self.partial_entries(), [])

    # BS-3
    def test_bs3_insufficient_space(self):
        # First backup: 2 x the database size (1 GiB) + 1024 MiB reserve = 3072 MiB needed.
        self.prepend("docker", rule(DB_SIZE, out(str(1024 * 1048576) + "\n")))
        self.prepend("df", rule(r".*", df_answer(3071 * 1024)))
        self.backup()
        self.assertExit(1)
        self.assertIn(f"backup: refused — 3071 MiB free in {sh_path(self.backup_dir)}, 3072 MiB needed. Nothing was written.",
                      self.result.stderr)
        self.assertNoDocker(r"sh -c pg_dump")
        self.assertEqual(self.partial_entries(), [])
        self.assertFalse(self.lock.exists())
        with self.subTest(case="later backups: 2 x the newest dump"):
            self.reset()
            old = self.backup_dir / "20261007T020000Z-daily"
            old.mkdir()
            (old / "partflow.dump").write_bytes(b"P" * (3 * 1048576))
            self.prepend("df", rule(r".*", df_answer(15 * 1024)))
            self.backup("--reserve-mib", "10")
            self.assertExit(1)
            self.assertIn("15 MiB free", self.result.stderr)
            self.assertIn("16 MiB needed", self.result.stderr)
            self.assertNoDocker(DB_SIZE, r"pg_dump")

    # BS-4
    def test_bs4_dump_fails(self):
        for name, answer in (("exit 1", out("", rc=1, stderr="pg_dump: error: connection failed\n")), ("empty", out(""))):
            with self.subTest(case=name):
                self.reset()
                self.prepend("docker", rule(PG_DUMP, answer))
                self.backup()
                self.assertExit(3)
                self.assertIn("No backup was published.", self.result.stderr)
                self.assertEqual(self.partial_entries(), [])
                self.assertEqual(self.published(), [])
                self.assertNoDocker(r"backup-tools", PG_LIST)
                self.assertFalse(self.lock.exists())

    # BS-5
    def test_bs5_manifest_refused(self):
        self.prepend("docker", rule(TOOL_RUN + "backup-manifest ", out(report("backup-manifest", "refused", 1), rc=1)))
        self.backup()
        self.assertExit(3)
        self.assertEqual(self.partial_entries(), [])
        self.assertEqual(self.published(), [])
        self.assertNoDocker(r"backup-verify", r"backup-rotate")

    # BS-6
    def test_bs6_verify_fails_after_publish(self):
        self.prepend("docker", rule(TOOL_RUN + "backup-verify ", out(report("backup-verify", "invalid", 1), rc=1)))
        self.backup()
        self.assertExit(3)
        published = self.published()
        self.assertEqual(len(published), 1)
        self.assertIn(f"Do not use {published[0]}; remove it after review.", self.result.stderr)
        self.assertEqual(self.result.stdout, "")
        self.assertNoDocker(r"backup-rotate")
        self.assertFalse(self.lock.exists())

    # BS-7
    def test_bs7_kinds(self):
        self.backup("--label", TOOLS, "--tools-release", TOOLS, kind="pre-release")
        self.assertExit(0)
        name, _ = self.name()
        self.assertEqual(name[16:], f"-pre-release-{TOOLS}")
        manifest = self.docker_entries(r"backup-manifest ")[0]
        self.assertIn("--reason pre-release backup for v1.1.0 --release-tag v1.0.0",
                      " ".join(manifest["argv"]))
        self.assertEqual(manifest["env"].get("PARTFLOW_RELEASE"), TOOLS)
        self.assertNoDocker(r"backup-rotate")
        for arguments, kind in ((("--label", TOOLS), "manual"), ((), "manual"), (("--label", TOOLS), "daily"),
                                ((), "pre-release"), (("--label", "bad tag"), "pre-release")):
            with self.subTest(kind=kind, arguments=arguments):
                self.reset()
                extra = ("--reason", "x") if kind == "manual" and arguments else ()
                self.backup(*arguments, *extra, kind=kind)
                self.assertExit(2)
                self.assertEqual(self.calls(), [])
        with self.subTest(case="manual with a reason"):
            self.reset()
            self.backup("--reason", "before the NAS move", kind="manual")
            self.assertExit(0)
            self.assertRegex(self.name()[0], r"-manual$")

    # BS-8, BS-17
    def test_bs8_bs17_rotation_problems(self):
        for rc in (2, 1):
            with self.subTest(rc=rc):
                self.reset()
                self.prepend("docker", rule(TOOL_RUN + "backup-rotate ", out(report(
                    "backup-rotate", "rotated_with_invalid" if rc == 1 else "failed", rc,
                    invalid=[{"name": "20261001T020000Z-daily", "check": "sha256sums", "detail": "partflow.dump: sha256 differs"}]
                    if rc == 1 else []), rc=rc)))
                self.backup()
                self.assertExit(4)
                name, _ = self.name()
                self.assertEqual(self.published(), [name])
                if rc == 1:
                    self.assertIn("20261001T020000Z-daily", self.result.stderr)
                    self.assertIn("kept for review (OPERATIONS_RUNBOOK §9)", self.result.stderr)
                else:
                    self.assertIn("rotation failed", self.result.stderr)
                self.assertFalse(self.lock.exists())

    # BS-9
    def test_bs9_db_not_running(self):
        self.prepend("docker", rule(r"ps --status running -q db$", out("")))
        self.backup()
        self.assertExit(2)
        self.assertNoDocker(r"^exec ", r"exec -T")
        self.assertFalse(self.lock.exists())

    # BS-10
    def test_bs10_revision_unreadable(self):
        self.prepend("docker", rule(r"app\.cli revision$", out("", rc=2, stderr="could not connect\n")))
        self.backup()
        self.assertExit(0)
        argv = self.docker_entries(r"backup-manifest ")[0]["argv"]
        self.assertEqual(argv[argv.index("--expected-revision") + 1], "")
        self.assertIn("expected revision could not be read", self.result.stderr)

    # BS-11
    def test_bs11_rehearsal_arguments(self):
        for arguments in (("--rehearsal",), ("--rehearsal", "--project", "partflow-production"), ("--project", "x")):
            with self.subTest(arguments=arguments):
                self.backup(*arguments)
                self.assertExit(2)
                self.assertEqual(self.calls(), [])
        with self.subTest(case="rehearsal"):
            self.backup("--rehearsal", "--project", "partflow-s5-x")
            self.assertExit(0)
            for entry in self.calls("docker"):
                if entry["argv"][0] == "compose":
                    self.assertEqual(entry["argv"][:3], ["compose", "-p", "partflow-s5-x"])
            self.assertIn("--environment rehearsal", " ".join(self.docker_entries(r"backup-manifest ")[0]["argv"]))

    # BS-12
    def test_bs12_interrupted_during_the_dump(self):
        self.prepend("docker", rule(PG_DUMP, out("PGDMP partial", signal="TERM")))
        self.backup()
        self.assertExit(3)
        self.assertIn("interrupted during step dump", self.result.stderr)
        self.assertEqual(self.partial_entries(), [])
        self.assertEqual(self.published(), [])
        self.assertFalse(self.lock.exists())
        self.assertNoDocker(r"backup-tools")

    # BS-13
    def test_bs13_stale_lock(self):
        owner = self.write_lock()
        partial = self.backup_dir / ".partial" / "20261008T020000Z-daily"
        partial.mkdir(parents=True)
        (partial / "partflow.dump").write_text("PGDMP", encoding="utf-8")
        self.prepend("ps", rule(r"^-p 4242$", out("", rc=1)))
        self.backup()
        self.assertExit(1)
        bd = sh_path(self.backup_dir)
        self.assertIn(
            "backup: refused — the backup lock of backup.sh (host bs-host, pid 4242, since 2026-10-08T02:00:00Z, backup"
            " 20261008T020000Z-daily) was left by a run that no longer exists. Check OPERATIONS_RUNBOOK §3, then remove"
            f" {bd}/.backup.lock and {bd}/.partial/20261008T020000Z-daily. Nothing was written.", self.result.stderr)
        self.assertEqual((self.lock / "owner").read_text(encoding="utf-8"), owner)
        self.assertEqual(self.partial_entries(), ["20261008T020000Z-daily"])
        self.assertTrue((partial / "partflow.dump").exists())
        self.assertNoDocker(r"sh -c pg_dump", r"backup-tools")
        self.assertEqual(len(self.docker_entries(PG_STAT)), 1)

    # BS-14
    def test_bs14_lock_of_a_live_run(self):
        for name, ps_rules, sessions in (("pid alive", [], "0\n"), ("pg_dump session", [rule(r"^-p 4242$", out("", rc=1))], "1\n"),
                                         ("sessions unreadable", [rule(r"^-p 4242$", out("", rc=1))], None)):
            with self.subTest(case=name):
                self.reset()
                owner = self.write_lock()
                self.prepend("ps", *ps_rules)
                self.prepend("docker", rule(PG_STAT, out(sessions) if sessions is not None else out("", rc=1)))
                self.backup()
                self.assertExit(1)
                self.assertIn("backup: refused — a backup is running (backup.sh on bs-host, pid 4242", self.result.stderr)
                self.assertEqual((self.lock / "owner").read_text(encoding="utf-8"), owner)
                self.assertNoDocker(r"sh -c pg_dump")
        with self.subTest(case="ps cannot look up a pid"):
            self.reset()
            owner = self.write_lock()
            self.prepend("ps", rule(r".*", out("", rc=1)))
            self.backup()
            self.assertExit(1)
            self.assertIn("a backup is running", self.result.stderr)
            self.assertNoDocker(PG_STAT)

    # BS-15
    def test_bs15_lock_held_by_a_release(self):
        owner = self.write_lock(by="release.sh", extra=("release=/home/ops/partflow-deployments/20261008T100000Z-v1.1.0",))
        self.backup()
        self.assertExit(1)
        self.assertIn("backup: refused — a backup is running (release.sh on bs-host, pid 4242, since 2026-10-08T02:00:00Z,"
                      " release record /home/ops/partflow-deployments/20261008T100000Z-v1.1.0). Nothing was written.",
                      self.result.stderr)
        self.assertEqual((self.lock / "owner").read_text(encoding="utf-8"), owner)

    # BS-16
    def test_bs16_lock_held_by_release(self):
        record = "/home/ops/partflow-deployments/20261008T100000Z-v1.1.0"
        owner = self.write_lock(by="release.sh", extra=(f"release={record}",))
        self.backup("--label", TOOLS, "--tools-release", TOOLS, "--lock-held-by-release", record, kind="pre-release")
        self.assertExit(0)
        self.assertEqual((self.lock / "owner").read_text(encoding="utf-8"), owner)
        with self.subTest(case="TERM"):
            for published in self.published():
                shutil.rmtree(self.backup_dir / published)
            self.prepend("docker", rule(PG_DUMP, out("PGDMP", signal="TERM")))
            self.backup("--label", TOOLS, "--lock-held-by-release", record, kind="pre-release")
            self.assertExit(3)
            self.assertEqual((self.lock / "owner").read_text(encoding="utf-8"), owner)
        for name, setup in (("no lock", lambda: shutil.rmtree(self.lock)),
                            ("another record", lambda: None)):
            with self.subTest(case=name):
                self.reset()
                self.write_lock(by="release.sh", extra=("release=/home/ops/partflow-deployments/other",))
                setup()
                self.backup("--label", TOOLS, "--lock-held-by-release", record, kind="pre-release")
                self.assertExit(2)
                self.assertNoDocker(r"sh -c pg_dump")
                self.assertEqual(self.lock.exists(), name != "no lock")

    def test_env_and_checkout_checks(self):
        cases = {
            "relative backup dir": [line if not line.startswith("PARTFLOW_BACKUP_DIR=") else "PARTFLOW_BACKUP_DIR=backups"
                                    for line in self.env_lines],
            "quoted backup dir": [line if not line.startswith("PARTFLOW_BACKUP_DIR=") else f'PARTFLOW_BACKUP_DIR="{sh_path(self.backup_dir)}"'
                                  for line in self.env_lines],
            "missing backup dir": [line if not line.startswith("PARTFLOW_BACKUP_DIR=") else f"PARTFLOW_BACKUP_DIR={sh_path(self.tmp)}/none"
                                   for line in self.env_lines],
            "no release": [line for line in self.env_lines if not line.startswith("PARTFLOW_RELEASE=")],
        }
        for name, lines in cases.items():
            with self.subTest(case=name):
                self.write_env(lines)
                self.backup()
                self.assertExit(2)
                self.assertEqual(self.calls("docker"), [])
        with self.subTest(case="not a release checkout"):
            self.write_env(self.env_lines)
            (self.root / "compose.production.yaml").unlink()
            self.backup()
            self.assertExit(2)
            self.assertIn("compose.production.yaml not found", self.result.stderr)

    def test_warnings_are_repeated(self):
        self.prepend("docker", rule(TOOL_RUN + "backup-manifest ", {
            **out(report("backup-manifest", "published", 0, warnings=["extra tables in the dump: s4_extra"])),
            "run": PUBLISH.format(bd=str(self.backup_dir))}))
        self.backup()
        self.assertExit(0)
        self.assertIn("backup: warning — extra tables in the dump: s4_extra", self.result.stderr)


# ---------------------------------------------------------------------------
# restore-test.sh (RT-1..RT-19)
# ---------------------------------------------------------------------------

DRILL_PROJECT = "partflow-restore-rt"
BACKUP_NAME = "20261008T020000Z-daily"
DR_PREFIX = re.compile(r"^compose -f compose\.production\.yaml -f \S*restore-test\.override\.yaml --env-file \S*restore-test\.env"
                       r" -p (\S+) ")


def verify_doc(exit_code=0, release=RELEASE, matches=True):
    return report("backup-verify", "verified" if exit_code == 0 else "invalid", exit_code, name=BACKUP_NAME, kind="daily",
                  release_tag=release, alembic_revision=REVISION, database_name="partflow",
                  dump_started_at="2026-10-08T02:00:01Z", dump_bytes=29, manifest_sha256="e" * 64,
                  release_matches_revision=matches, checks=[], extra_entries=[], extra_tables=[], alembic_rows=[REVISION],
                  error=None)


def drill_reconcile_doc(exit_code=0, h="pass", j="pass", findings=()):
    checks = [{"id": check_id, "title": check_id, "status": "pass", "duration_ms": 1, "examined": {}, "finding_count": 0,
               "truncated": False, "reason": None, "error_code": None, "findings": []} for check_id in "abcdefgi"]
    checks.append({"id": "h", "title": "h", "status": h, "duration_ms": 1, "examined": {}, "finding_count": 0,
                   "truncated": False, "reason": None, "error_code": None, "findings": []})
    j_findings = [{"code": code, "entity": {"type": kind, "id": ident}, "part_number": None, "expected": None,
                   "actual": None, "detail": {}} for code, kind, ident in findings]
    checks.append({"id": "j", "title": "j", "status": j, "duration_ms": 1, "examined": {}, "finding_count": len(j_findings),
                   "truncated": False, "reason": None, "error_code": None, "findings": j_findings})
    return json.dumps({
        "report_version": 1, "command": "reconcile", "result": "clean" if exit_code == 0 else "mismatch",
        "exit_code": exit_code, "started_at": "2026-10-08T10:00:00Z", "finished_at": "2026-10-08T10:00:01Z",
        "duration_ms": 1000, "runtime": {},
        "database": {"name": "partflow_restore_test", "server_version": "16.14 (Debian 16.14-1.pgdg13+1)",
                     "alembic_revision": REVISION, "collation": "en_US.utf8", "ctype": "en_US.utf8",
                     "collation_version_recorded": "2.41", "collation_version_actual": "2.41",
                     "connected_role": "partflow_app", "connected_role_superuser": False},
        "options": {}, "error": None, "checks": checks,
    }, indent=2, ensure_ascii=True) + "\n"


class RestoreDrill(ScriptHarness):
    def setUp(self):
        super().setUp()
        # Run the copy beside a stub smoke.sh; reconcile_regression.py is the real one.
        scripts = self.root / "deploy" / "production"
        scripts.mkdir(parents=True)
        for name in ("restore-test.sh", "reconcile_regression.py"):
            shutil.copyfile(REPO / "deploy" / "production" / name, scripts / name)
        self.restore_sh = scripts / "restore-test.sh"
        self.wrapper(scripts / "smoke.sh", f'exec "{self.python}" "{self.fake.as_posix()}" "{self.state.as_posix()}" smoke.sh "$@"')
        self.rules["smoke.sh"] = [rule(r".*", out("PASS S-1 ...\n"))]
        backup = self.backup_dir / BACKUP_NAME
        backup.mkdir()
        (backup / "partflow.dump").write_bytes(b"PGDMP drill test dump bytes")
        records = self.records.as_posix()
        self.rules["docker"] = [
            rule(r"^ps -a -q --filter label=com\.docker\.compose\.project="),
            rule(r"^volume ls -q --filter label=com\.docker\.compose\.project="),
            rule(r"^info --format \{\{\.DockerRootDir\}\}$", out("/var/lib/docker\n")),
            rule(r" config --quiet$"),
            rule(r"backup-tools backup-verify ", out(verify_doc())),
            rule(r"^image inspect "),
            rule(r" up -d --wait db$"),
            rule(r" ps -q db$", out("cid-drill-db\n")),
            rule(r"^inspect --format \{\{\.Image\}\} cid-drill-db$", out(IMAGE_DB + "\n")),
            rule(r" exec -T db sh -c pg_restore ", {"read_stdin": True}),
            rule(r" --profile ops run --rm -T db-roles$", out(report("provision-roles", "provisioned", 0))),
            rule(r" --profile ops run --rm -T db-roles apply-grants$", out(report("apply-grants", "applied", 0))),
            rule(r" run --rm --no-deps -T backend python -m app\.cli revision$",
                 out(json.dumps({"report_version": 1, "command": "revision", "state": "current", "exit_code": 0,
                                 "release": RELEASE, "database_revision": REVISION, "expected_revision": REVISION},
                                indent=2) + "\n")),
            rule(r" up -d backend web$"),
            rule(r" run --rm --no-deps -T backend python -m app\.cli reconcile --max-findings 10000$", out(drill_reconcile_doc())),
            rule(r" down -v --remove-orphans$", {
                "run": f"import glob\nopen(os.path.join({self.state.as_posix()!r}, 'down-evidence.txt'), 'w')"
                       f".write(str(bool(glob.glob(os.path.join({records!r}, '*', 'evidence.json')))))"}),
        ]
        self.rules["curl"] = [rule(r"^GET /api/health$", {"status": 200, "body": health_body(release=RELEASE), "headers": {}})]

    def drill(self, *arguments, project=DRILL_PROJECT, backup=BACKUP_NAME):
        base = ["--backup", backup, "--operator", "Ops Person", "--records-dir", sh_path(self.records)]
        if project:
            base += ["--project", project]
        return self.run_script(self.restore_sh, *base, *arguments)

    def labels(self):
        """Docker calls with the drill's Compose prefix shortened to `DR <project>`."""
        result = []
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            match = DR_PREFIX.match(text)
            result.append(f"DR[{match.group(1)}] " + text[match.end():] if match else text)
        return result

    def evidence_dir(self):
        found = sorted(self.records.glob(f"*-restore-test-{BACKUP_NAME}"))
        self.assertEqual(len(found), 1, f"{found}\n{self.result.stdout}\n{self.result.stderr}")
        return found[0]

    def evidence(self):
        return json.loads((self.evidence_dir() / "evidence.json").read_text(encoding="utf-8"))

    def teardown_record(self):
        return json.loads((self.evidence_dir() / "teardown.json").read_text(encoding="utf-8"))

    def assertNoProjectTouched(self):
        self.assertFalse([c for c in self.labels() if c.startswith("DR[") and re.search(r" (up|down) ", c)], self.labels())

    # RT-1, RT-12, RT-17
    def test_rt1_all_green(self):
        self.drill()
        self.assertExit(0)
        dr = f"DR[{DRILL_PROJECT}] "
        self.assertEqual(self.labels(), [
            f"ps -a -q --filter label=com.docker.compose.project={DRILL_PROJECT}",
            f"volume ls -q --filter label=com.docker.compose.project={DRILL_PROJECT}",
            "info --format {{.DockerRootDir}}",
            dr + "config --quiet",
            dr + f"--profile ops run --rm --no-deps -T --user 1000:1000 backup-tools backup-verify {BACKUP_NAME}",
            f"image inspect --format {{{{.Id}}}} partflow/backend:{RELEASE} partflow/web:{RELEASE} partflow/backend:{RELEASE}",
            dr + "up -d --wait db",
            dr + "ps -q db",
            "inspect --format {{.Image}} cid-drill-db",
            dr + 'exec -T db sh -c pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error'
                 " --no-owner --no-privileges",
            dr + "--profile ops run --rm -T db-roles",
            dr + "--profile ops run --rm -T db-roles apply-grants",
            dr + "run --rm --no-deps -T backend python -m app.cli revision",
            dr + "up -d backend web",
            dr + "run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000",
            dr + "down -v --remove-orphans",
        ])
        restore = self.docker_entries(r"pg_restore")[0]
        self.assertEqual(restore["stdin"], {"bytes": 27, "head": "PGDMP drill test"})
        self.assertEqual([c["request"] for c in self.calls("curl")], ["GET /api/health"])
        self.assertEqual(self.calls("smoke.sh")[0]["argv"][:2], ["--release", RELEASE])
        self.assertEqual(self.calls("smoke.sh")[0]["argv"][4:], ["--project", DRILL_PROJECT])
        evidence = self.evidence()
        self.assertEqual(list(evidence), ["evidence_version", "command", "outcome", "failed_step", "exit_code", "operator",
                                          "host", "started_at", "finished_at", "backup", "drill", "server", "timings_ms",
                                          "steps", "reconcile", "smoke", "limitations", "kept"])
        self.assertEqual((evidence["outcome"], evidence["exit_code"], evidence["failed_step"]), ("passed", 0, None))
        self.assertEqual(evidence["backup"], {"name": BACKUP_NAME, "path": f"{sh_path(self.backup_dir)}/{BACKUP_NAME}",
                                              "dump_bytes": 27, "alembic_revision": REVISION, "release": RELEASE,
                                              "dump_started_at": "2026-10-08T02:00:01Z", "manifest_sha256": "e" * 64})
        self.assertEqual(evidence["drill"], {"project": DRILL_PROJECT, "release": RELEASE, "tools_release": RELEASE,
                                             "db_image_requested": None, "db_image_id": IMAGE_DB,
                                             "database": "partflow_restore_test", "http_port": 18090,
                                             "edge_subnet": "172.30.254.0/24"})
        self.assertEqual(evidence["server"]["collation_version_actual"], "2.41")
        self.assertEqual(set(evidence["timings_ms"]), {"verify", "db_start", "restore", "roles_and_grants",
                                                       "app_start_to_ready", "reconcile", "smoke", "restore_to_ready", "total"})
        self.assertTrue(all(isinstance(v, int) for v in evidence["timings_ms"].values()), evidence["timings_ms"])
        self.assertEqual([s["name"] for s in evidence["steps"]],
                         ["preflight", "verify", "images", "db_start", "restore", "roles", "grants", "revision",
                          "app_start", "reconcile", "smoke"])
        self.assertEqual(evidence["reconcile"], {"file": "reconcile.json", "exit_code": 0, "checks": {"h": "pass", "j": "pass"},
                                                 "baseline": None, "regression": None})
        self.assertEqual(evidence["smoke"], {"file": "smoke.txt", "exit_code": 0})
        self.assertEqual(evidence["limitations"], {"read_model_readback": "not_automated"})
        self.assertIs(evidence["kept"], False)
        self.assertEqual(self.teardown_record()["teardown"], "removed")
        self.assertEqual((self.state / "down-evidence.txt").read_text(encoding="utf-8"), "True")
        # RT-12: three distinct 64-hex throwaway passwords, removed after the teardown; the env file has every key.
        work = self.evidence_dir() / "work"
        self.assertFalse((work / "secrets").exists())
        env = dict(line.split("=", 1) for line in (work / "restore-test.env").read_text(encoding="utf-8").splitlines())
        example = {line.split("=", 1)[0] for line in (REPO / ".env.production.example").read_text(encoding="utf-8").splitlines()
                   if re.match(r"^[A-Z_]+=", line)}
        self.assertEqual(set(env), example)
        self.assertEqual((env["PARTFLOW_RELEASE"], env["POSTGRES_DB"], env["PARTFLOW_BACKEND_WORKERS"], env["PARTFLOW_HTTP_PORT"]),
                         (RELEASE, "partflow_restore_test", "1", "18090"))
        self.assertEqual(env["PARTFLOW_BACKUP_DIR"], sh_path(self.backup_dir))
        self.assertTrue(env["PARTFLOW_SECRETS_DIR"].endswith("/work/secrets"))
        self.assertEqual(env["PARTFLOW_DB_MEMORY"], "1g")
        self.assertEqual(env["PARTFLOW_WEB_MEMORY"], "")
        self.assertRestoreTeardownOnlyOnDrill()

    def assertRestoreTeardownOnlyOnDrill(self):
        # RT-17: every down names the drill project, never partflow-production.
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            if " down " in f" {text} ":
                self.assertRegex(text, r" -p partflow-restore-")
                self.assertNotIn("partflow-production", text)

    # RT-12
    def test_rt12_generated_secrets(self):
        self.prepend("docker", rule(r" config --quiet$", {"run": (
            "import glob, stat\n"
            f"files = sorted(glob.glob(os.path.join({self.records.as_posix()!r}, '*', 'work', 'secrets', '*')))\n"
            f"open(os.path.join({self.state.as_posix()!r}, 'secrets.json'), 'w').write(json.dumps("
            "[[os.path.basename(f), open(f).read(), stat.S_IMODE(os.stat(f).st_mode)] for f in files]))\n")}))
        self.drill()
        self.assertExit(0)
        secrets = json.loads((self.state / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual([s[0] for s in secrets], ["partflow_app_password", "partflow_maintenance_password", "postgres_password"])
        values = [s[1].strip() for s in secrets]
        for value in values:
            self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertEqual(len(set(values)), 3)
        if os.name != "nt":
            self.assertEqual({s[2] for s in secrets}, {0o444})
            self.assertEqual(os.stat(self.evidence_dir() / "work").st_mode & 0o777, 0o700)
        self.assertFalse((self.evidence_dir() / "work" / "secrets").exists())

    # RT-2, RT-16
    def test_rt2_rt16_names_are_checked_first(self):
        for project in ("partflow-production", "partflow-staging", "restore-1", "partflow-restore-", "partflow-restore-X"):
            with self.subTest(project=project):
                self.drill(project=project)
                self.assertExit(2)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.records.exists())
        for backup in ("../x", "20261008T020000Z-daily/../../y", "20261008T020000Z-daily-label", "x"):
            with self.subTest(backup=backup):
                self.drill(backup=backup)
                self.assertExit(2)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.records.exists())

    # RT-3
    def test_rt3_never_the_production_names(self):
        cases = {
            "database": [line if not line.startswith("POSTGRES_DB=") else "POSTGRES_DB=partflow_restore_test" for line in self.env_lines],
            "port": [line if not line.startswith("PARTFLOW_HTTP_PORT=") else "PARTFLOW_HTTP_PORT=18090" for line in self.env_lines],
            "subnet": [line if not line.startswith("PARTFLOW_EDGE_SUBNET=") else "PARTFLOW_EDGE_SUBNET=172.30.254.0/24"
                       for line in self.env_lines],
        }
        for name, lines in cases.items():
            with self.subTest(case=name):
                self.write_env(lines)
                self.drill()
                self.assertExit(2)
                self.assertNoProjectTouched()
                self.assertFalse(self.records.exists())

    # RT-4
    def test_rt4_existing_project(self):
        for pattern in (r"^ps -a -q --filter", r"^volume ls -q --filter"):
            with self.subTest(existing=pattern):
                self.reset()
                self.prepend("docker", rule(pattern, out("abc123\n")))
                self.drill()
                self.assertExit(2)
                self.assertIn(f"docker compose -p {DRILL_PROJECT} down -v", self.result.stderr)
                self.assertNoProjectTouched()
                self.assertFalse(self.records.exists())

    # RT-5
    def test_rt5_insufficient_space(self):
        self.prepend("df", rule(r".*", df_answer(1024 * 1024)))  # 1024 MiB < 5 x 1 MiB + 1024 MiB
        self.drill()
        self.assertExit(1)
        self.assertIn("1024 MiB free on the Docker root /var/lib/docker, 1029 MiB needed", self.result.stderr)
        self.assertNoProjectTouched()
        with self.subTest(case="df cannot read the Docker root"):
            self.reset()
            self.prepend("df", rule(r".*", out("", rc=1)))
            self.drill()
            self.assertExit(2)
            self.assertIn("the drill needs that check", self.result.stderr)

    # RT-6
    def test_rt6_verify_fails(self):
        self.prepend("docker", rule(r"backup-tools backup-verify ", out(verify_doc(exit_code=1), rc=1)))
        self.drill()
        self.assertExit(1)
        evidence = self.evidence()
        self.assertEqual((evidence["outcome"], evidence["failed_step"]), ("failed", "verify"))
        self.assertNoDocker(r" up ")
        self.assertEqual(self.teardown_record()["teardown"], "not_created")
        self.assertNoDocker(r" down ")

    # RT-7, RT-7b
    def test_rt7_restore_fails(self):
        self.prepend("docker", rule(r" exec -T db sh -c pg_restore ", {"read_stdin": True, "rc": 1,
                                                                        "stderr": "pg_restore: error: could not execute query\n"}))
        self.drill()
        self.assertExit(1)
        evidence = self.evidence()
        self.assertEqual((evidence["outcome"], evidence["failed_step"]), ("failed", "restore"))
        self.assertEqual((self.state / "down-evidence.txt").read_text(encoding="utf-8"), "True")
        argv = self.docker_entries(r"pg_restore")[0]["argv"]
        self.assertIn("--single-transaction --exit-on-error --no-owner --no-privileges", argv[-1])
        self.assertNoDocker(r"db-roles", r"up -d backend web")
        self.assertEqual(self.teardown_record()["teardown"], "removed")

    # RT-8
    def test_rt8_reconcile_findings(self):
        finding = ("collation_identity", "part_number", "PN-1")
        new = ("collation_identity", "part_number", "PN-2")
        baseline = self.tmp / "pre-reconcile.json"
        baseline.write_text(drill_reconcile_doc(1, j="fail", findings=[finding]), encoding="utf-8")
        cases = (
            ("no baseline", [], [finding], "failed", 1),
            ("baseline, pre-existing only", ["--baseline-report", sh_path(baseline)], [finding], "passed_with_preexisting_findings", 0),
            ("baseline, a new finding", ["--baseline-report", sh_path(baseline)], [finding, new], "failed", 1),
        )
        for name, extra, findings, outcome, code in cases:
            with self.subTest(case=name):
                self.reset()
                baseline.parent.mkdir(parents=True, exist_ok=True)
                baseline.write_text(drill_reconcile_doc(1, j="fail", findings=[finding]), encoding="utf-8")
                self.prepend("docker", rule(r"app\.cli reconcile ", out(drill_reconcile_doc(1, j="fail", findings=findings), rc=1)))
                self.drill(*extra)
                self.assertExit(code)
                evidence = self.evidence()
                self.assertEqual(evidence["outcome"], outcome)
                self.assertEqual(evidence["reconcile"]["checks"], {"h": "pass", "j": "fail"})
                if extra:
                    self.assertEqual(evidence["reconcile"]["regression"]["file"], "regression.txt")
                    self.assertEqual(evidence["reconcile"]["regression"]["exit_code"], 0 if code == 0 else 1)
                    self.assertEqual(evidence["reconcile"]["baseline"], sh_path(baseline))
                self.assertEqual(bool(self.calls("smoke.sh")), code == 0)

    # RT-9
    def test_rt9_keep(self):
        self.drill("--keep")
        self.assertExit(0)
        self.assertNoDocker(r" down ")
        record = self.teardown_record()
        self.assertEqual(record["teardown"], "kept")
        self.assertEqual(record["command"], f"docker compose -p {DRILL_PROJECT} down -v --remove-orphans")
        self.assertIn(f"docker compose -p {DRILL_PROJECT} down -v", self.result.stderr)
        self.assertIs(self.evidence()["kept"], True)

    # RT-10
    def test_rt10_teardown_fails(self):
        self.prepend("docker", rule(r" down -v --remove-orphans$", out("", rc=1, stderr="down failed\n")))
        self.drill()
        self.assertExit(3)
        self.assertEqual(self.evidence()["outcome"], "passed")
        self.assertEqual(self.teardown_record()["teardown"], "failed")
        self.assertIn(f"docker compose -p {DRILL_PROJECT} down -v", self.result.stderr)

    # RT-11: the generated override resolved by the real Compose (no daemon call).
    def test_rt11_db_image_and_read_only_backups(self):
        self.prepend("docker", rule(r"backup-tools backup-verify ", out(verify_doc(exit_code=1), rc=1)))
        self.drill("--db-image", "postgres:16.14-bookworm", "--tools-release", TOOLS)
        self.assertExit(1)
        work = self.evidence_dir() / "work"
        override = (work / "restore-test.override.yaml").read_text(encoding="utf-8")
        self.assertIn('image: "postgres:16.14-bookworm"', override)
        secrets = work / "secrets"
        secrets.mkdir()
        for name in ("postgres_password", "partflow_app_password", "partflow_maintenance_password"):
            (secrets / name).write_text("x" * 64 + "\n", encoding="utf-8")
        env_text = (work / "restore-test.env").read_text(encoding="utf-8")
        env_text = re.sub(r"(?m)^PARTFLOW_SECRETS_DIR=.*$", f"PARTFLOW_SECRETS_DIR={secrets.as_posix()}", env_text)
        env_text = re.sub(r"(?m)^PARTFLOW_BACKUP_DIR=.*$", f"PARTFLOW_BACKUP_DIR={self.backup_dir.as_posix()}", env_text)
        (work / "resolved.env").write_text(env_text, encoding="utf-8")
        override = re.sub(r'source: ".*"', f'source: "{self.backup_dir.as_posix()}"', override)
        (work / "resolved.override.yaml").write_text(override, encoding="utf-8")
        environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(HOST_VARIABLE_PREFIXES)}
        result = subprocess.run(
            ["docker", "compose", "-f", str(REPO / "compose.production.yaml"), "-f", str(work / "resolved.override.yaml"),
             "--env-file", str(work / "resolved.env"), "-p", DRILL_PROJECT, "--profile", "ops", "config", "--format", "json"],
            cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertEqual(services["db"]["image"], "postgres:16.14-bookworm")
        self.assertEqual(services["backup-tools"]["image"], f"partflow/backend:{TOOLS}")
        for name in ("backup-tools", "migrate"):
            volumes = services[name]["volumes"]
            self.assertEqual(len(volumes), 1, name)
            self.assertEqual(volumes[0]["target"], "/backups")
            self.assertIs(volumes[0].get("read_only"), True, name)
            self.assertIsNot((volumes[0].get("bind") or {}).get("create_host_path"), True, name)
        self.assertEqual(services["backend"]["environment"]["DATABASE_NAME"], "partflow_restore_test")
        self.assertEqual(services["backend"]["environment"]["WEB_CONCURRENCY"], "1")

    # RT-13
    def test_rt13_interrupted(self):
        self.prepend("docker", rule(r" --profile ops run --rm -T db-roles$", out(report("provision-roles", "provisioned", 0),
                                                                              signal="TERM")))
        self.drill()
        self.assertExit(2)
        evidence = self.evidence()
        self.assertEqual((evidence["outcome"], evidence["failed_step"]), ("interrupted", "roles"))
        self.assertEqual(self.teardown_record()["teardown"], "removed")
        self.assertIn("interrupted during step roles", self.result.stderr)

    # RT-14
    def test_rt14_grants_revision_mismatch(self):
        self.prepend("docker", rule(r" db-roles apply-grants$", out(report(
            "apply-grants", "refused", 1, error={"code": "revision_mismatch", "message": "not at the head"}), rc=1)))
        self.drill()
        self.assertExit(1)
        self.assertEqual(self.evidence()["failed_step"], "grants")
        self.assertIn("Name the matching release with --release.", self.result.stderr)

    # RT-15
    def test_rt15_interrupted_while_the_project_exists(self):
        self.prepend("docker", rule(r"^ps -a -q --filter", out("abc123\n", signal="TERM")))
        self.drill()
        self.assertExit(2)
        self.assertNoProjectTouched()
        self.assertFalse(self.records.exists())

    # RT-18
    def test_rt18_check_statuses(self):
        for name, h, j, outcome in (("h not_applicable", "not_applicable", "pass", "failed"), ("j skipped", "pass", "skipped", "failed"),
                                    ("both pass", "pass", "pass", "passed")):
            with self.subTest(case=name):
                self.reset()
                self.prepend("docker", rule(r"app\.cli reconcile ", out(drill_reconcile_doc(0, h=h, j=j))))
                self.drill()
                evidence = self.evidence()
                self.assertEqual(evidence["outcome"], outcome)
                self.assertEqual(evidence["reconcile"]["checks"], {"h": h, "j": j})
                self.assertExit(0 if outcome == "passed" else 1)

    # RT-19
    def test_rt19_release_does_not_match_the_revision(self):
        self.prepend("docker", rule(r"backup-tools backup-verify ", out(verify_doc(matches=False))))
        self.drill()
        self.assertExit(2)
        self.assertEqual(self.evidence()["outcome"], "could_not_run")
        self.assertIn("name the matching release with --release", self.result.stderr)
        self.assertNoDocker(r" up ")
        with self.subTest(case="--release B"):
            self.reset()
            self.prepend("docker", rule(r"backup-tools backup-verify ", out(verify_doc(matches=False))))
            self.prepend("curl", rule(r"^GET /api/health$", {"status": 200, "body": health_body(release=TOOLS), "headers": {}}))
            self.drill("--release", TOOLS)
            self.assertExit(0)
            self.assertEqual(self.evidence()["drill"]["release"], TOOLS)
            self.assertIn(f"image inspect --format {{{{.Id}}}} partflow/backend:{TOOLS} partflow/web:{TOOLS} partflow/backend:{RELEASE}",
                          self.labels())
            env = (self.evidence_dir() / "work" / "restore-test.env").read_text(encoding="utf-8")
            self.assertIn(f"PARTFLOW_RELEASE={TOOLS}\n", env)

    def test_images_missing(self):
        self.prepend("docker", rule(r"^image inspect ", out("", rc=1, stderr="No such image\n")))
        self.drill()
        self.assertExit(2)
        self.assertEqual(self.evidence()["outcome"], "could_not_run")
        self.assertIn(f"keep or build the images of release {RELEASE}", self.result.stderr)
        self.assertNoDocker(r" up ")
        self.assertEqual(self.teardown_record()["teardown"], "not_created")


if __name__ == "__main__":
    unittest.main()
