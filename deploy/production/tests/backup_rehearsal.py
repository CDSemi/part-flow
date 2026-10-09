"""P16-S5 backup rehearsal: real images, synthetic data, throwaway Compose projects (P16-S5 SPEC section 6.4, cases
DR-1..DR-10).

Usage (from any directory, on a host with the docker CLI, Compose v2, git, curl, python3 and a POSIX sh):
  python deploy/production/tests/backup_rehearsal.py --evidence <path.json> [--log-dir DIR] [--keep]

Releases, built from temporary copies of the working tree (the repository is never modified), all with
PARTFLOW_COMMIT=$(git rev-parse HEAD) so release.sh --rehearsal reuses them:
  A = the checkout; B = A + the rehearsal-only no-op revision 9999_s3_rehearsal_noop; F = A + the rehearsal-only
  revision 9997_s5_rehearsal_finding (down_revision = head), whose upgrade rewrites the body of the guard function
  partflow_areas_forbid_barcode_change (a comment line only: same behaviour, another pg_proc.prosrc hash), so the
  post-release reconcile reports exactly one check (h) finding and touches no quantity row.
  A' = `git archive be8ef76 compose.production.yaml compose.production.build.yaml deploy/production` (the last pre-S5
  commit: no backup-tools, no backup mount) stands for a pre-S5 previous release and runs A's images.

Cases: DR-1 install A, synthetic data, a daily backup; DR-2 restore drill (+ image read-back); DR-3 drill onto a
candidate PostgreSQL image (postgres:16.14-bookworm, another glibc); DR-4 release F stops frozen, then rollback path 3
exactly as documented (P16-S5 SPEC section 4.9) from a shell without PARTFLOW_*/POSTGRES_* variables, step A in F's
checkout copy and step B in A'; DR-4b an atomic path-3 restore that fails; DR-4c the completed-release path-4 example
(release.sh B with the automatic backup); DR-5 a stale backup refused; DR-6 a tampered backup refused; DR-7 a backup
during writes and its drill; DR-8 two concurrent backups; DR-9 second host type (not_run); DR-10 is the separate
release_rehearsal.py / stack_smoke.py run (recorded by the caller).

Isolation: only the project `partflow-s5-rehearsal` (edge 172.30.255.0/24; every documented block addresses it through
COMPOSE_PROJECT_NAME and first asserts that `$PF ps -q db` is that project's db) and the drill projects
partflow-restore-s5-* it creates; it refuses any of them that already has containers or volumes, never touches the
development stack `partflow`, a `pfa34*`/`dind*` container or the OPS lane. In `finally` it runs `down -v
--remove-orphans` for its projects, removes its image tags (A, B, F) and a --db-image it pulled itself.

Host adaptations (Docker Desktop):
- Ownership: Docker Desktop shows every host-created file and directory of a bind-mounted Windows folder as owned by
  uid 0 inside a container, so `--user "$(id -u):$(id -g)"` with the MSYS uid (197609) cannot chmod the host-created
  .partial/<NAME> directory (backup-manifest io_error "Operation not permitted", observed in the first run). The
  scripts therefore run with an `id` shim first on PATH that prints 0 for `-u`/`-g` (the owner as the containers see
  it); on a Linux host the invoking account owns the directory and no shim is used.
- python3: on Windows `python3` may be the Store alias, not an interpreter; a `python3` wrapper of this interpreter is
  first on PATH for every script and documented block (restore-test.sh and path 3 need python3).
- Free space: `docker info` names the Docker root inside the Docker VM (/var/lib/docker), which the
host cannot `df`, so restore-test.sh stops `could_not_run` there (A4; recorded as DR-2a). The drills then run with a
`df` shim first on PATH that answers for that one path with `df -Pk /` measured inside a throwaway container (the same
VM filesystem) and passes every other path to the real `df`. On Windows the documented blocks keep the OS variables the
docker CLI needs (SystemRoot, USERPROFILE, ...); no PARTFLOW_* or POSTGRES_* variable is passed.

Evidence JSON: per case pass / fail / not_run with the observed values (never a password, token or cookie value), the
commands with exit codes and durations. Exit 0 = every automated case passed; 1 = a case failed or the run broke.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_rehearsal import (  # noqa: E402
    NOOP_REVISION,
    NOOP_TEMPLATE,
    ROLE_SECRETS,
    ROW_COUNTS_SQL,
    TOKEN_PATTERN,
    Rehearsal,
    alembic_head,
    copy_checkout,
    write_secret_files,
)
from stack_smoke import (  # noqa: E402
    IMPORT_HEADER,
    CaseFailure,
    Client,
    check,
    display,
    free_port,
    make_backup_dir,
    parse_report,
    posix_host_path,
)

REPO = Path(__file__).resolve().parents[3]
BACKUP_SH = REPO / "deploy" / "production" / "backup.sh"
RESTORE_SH = REPO / "deploy" / "production" / "restore-test.sh"
DEFAULT_PROJECT = "partflow-s5-rehearsal"
EDGE_SUBNET = "172.30.255.0/24"
DRILL_SUBNET = "172.30.254.0/24"
RELEASES = {"A": "s5-dr-a", "B": "s5-dr-b", "F": "s5-dr-f"}
FINDING_REVISION = "9997_s5_rehearsal_finding"
FINDING_FUNCTION = "partflow_areas_forbid_barcode_change"
PRE_S5_COMMIT = "be8ef7639e0292a96c07eadbd6904805c8ed1582"
PRE_S5_PATHS = ("compose.production.yaml", "compose.production.build.yaml", "deploy/production")
CANDIDATE_DB_IMAGE = "postgres:16.14-bookworm"
FORBIDDEN = ("partflow", "partflow-production", "partflow-staging")
FORBIDDEN_PREFIXES = ("pfa34", "dind")
WINDOWS_VARIABLES = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                     "PROGRAMDATA", "PROGRAMFILES", "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH", "USERNAME", "MSYSTEM")
PN = "S5-REHEARSAL-PN-1"
# A 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
FINDING_TEMPLATE = '''"""P16-S5 backup rehearsal only: one deliberate check (h) finding ({revision}); never committed."""
from alembic import op

revision = "{revision}"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A comment line at the start of the guard function's body: same behaviour, another prosrc hash.
    op.execute(
        "DO $s5$ DECLARE definition text; BEGIN"
        " SELECT pg_get_functiondef('public.{function}()'::regprocedure) INTO definition;"
        " EXECUTE regexp_replace(definition, '\\\\$function\\\\$', E'$function$\\\\n-- s5 rehearsal finding\\\\n');"
        " END $s5$"
    )


def downgrade() -> None:
    raise RuntimeError("rehearsal-only revision")
'''

# RUNBOOK §6 path 3 (P16-S5 SPEC section 4.9) — step A, in the candidate release's checkout. Placeholders <...> are the
# only substitution; the rehearsal stops when a guard line prints its "stop" text.
STEP_A = r'''
REC=<REC>; CAND=<CAND>; PREV=<PREV>
python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["writes_reopened_at"] is not None)' "$REC/record.json" \
    || echo "writes were reopened: path 4, not path 3"            # stop here unless the owner recorded otherwise
NAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup"]["name"])' "$REC/record.json")
BPATH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup"]["path"])' "$REC/record.json")
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF stop backend                                                  # keep or enter the write freeze
$PF ps --status running -q backend                                # must print nothing
(cd "$BPATH" && sha256sum -c SHA256SUMS)                          # host check, independent of any image
PARTFLOW_RELEASE="$CAND" $PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" backup-tools backup-verify "$NAME"
'''
# Step B, part 1 (to the reconcile), in the previous release's checkout. The documented comment "Edit $ENV: ..." is
# carried out by the three sed lines marked REHEARSAL.
STEP_B1 = r'''
BPATH=<BPATH>; REC=<REC>; PREV=<PREV>
ENV=.env.production
PF="docker compose -f compose.production.yaml --env-file $ENV"
OLDDB=$(sed -n 's/^POSTGRES_DB=//p' "$ENV"); PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV")
NEWDB="${OLDDB}_r$(date -u +%Y%m%d%H%M)"                          # never the live name; createdb refuses an existing one
# Space: the database volume must hold the restored copy, the WAL of its transaction and a reserve.
FREE_KIB=$($PF exec -T db sh -c 'df -Pk /var/lib/postgresql/data' | awk 'NR==2 {print $4}')
DB_BYTES=$($PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_database_size(current_database())"')
DUMP_BYTES=$(wc -c < "$BPATH/partflow.dump")
[ $((FREE_KIB * 1024)) -ge $((DB_BYTES + 2 * DUMP_BYTES + 1073741824)) ] \
    || echo "not enough space: expand the storage first, or follow path 4"   # stop here when printed
$PF exec -T db sh -c 'createdb -U "$POSTGRES_USER" --template=template0 "$1"' sh "$NEWDB"
$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --single-transaction --exit-on-error --no-owner --no-privileges' sh "$NEWDB" \
    < "$BPATH/partflow.dump"
# Edit $ENV: PARTFLOW_RELEASE=$PREV, POSTGRES_DB=$NEWDB, PARTFLOW_ACCEPT_SCHEMA_REVISION=   (empty)
sed "s/^PARTFLOW_RELEASE=.*/PARTFLOW_RELEASE=$PREV/" "$ENV" > "$ENV.tmp" && mv "$ENV.tmp" "$ENV"                      # REHEARSAL
sed "s/^POSTGRES_DB=.*/POSTGRES_DB=$NEWDB/" "$ENV" > "$ENV.tmp" && mv "$ENV.tmp" "$ENV"                              # REHEARSAL
sed "s/^PARTFLOW_ACCEPT_SCHEMA_REVISION=.*/PARTFLOW_ACCEPT_SCHEMA_REVISION=/" "$ENV" > "$ENV.tmp" && mv "$ENV.tmp" "$ENV"  # REHEARSAL
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current"
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json"; echo "reconcile exit $?"
echo "NEWDB=$NEWDB OLDDB=$OLDDB"
'''
# Step B, part 2 (after the owner's approval was recorded in rollback.json).
STEP_B2 = r'''
PREV=<PREV>
ENV=.env.production
PF="docker compose -f compose.production.yaml --env-file $ENV"
PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV")
$PF up -d db backend                                              # db recreated for the new POSTGRES_DB (same volume)
for i in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" && break; sleep 2; done   # REHEARSAL: wait
curl -fsS "http://127.0.0.1:$PORT/api/health"                    # "release":"<previous>", "schema":"current"
$PF up -d --no-deps web                                           # the reopen (S3 DV-8 order)
deploy/production/smoke.sh --release "$PREV"
'''
PROJECT_GUARD = r'''
PF="docker compose -f compose.production.yaml --env-file .env.production"
db=$($PF ps -q db)
[ -n "$db" ] && [ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$db")" = "<PROJECT>" ] \
    || { echo "PROJECT GUARD: \$PF ps -q db is not the rehearsal project's db"; exit 99; }
'''


class BackupRehearsal(Rehearsal):
    releases = RELEASES
    edge_subnet = EDGE_SUBNET

    def __init__(self, args, evidence):
        super().__init__(args, project=args.project, evidence=evidence)
        self.drills = []
        self.pulled = []
        self.serving = RELEASES["A"]
        self.use_shims = True
        self.log_dir = Path(args.log_dir) if args.log_dir else None

    # -- setup ----------------------------------------------------------------

    def refuse_foreign_project(self):
        if self.project in FORBIDDEN or self.project.startswith(FORBIDDEN_PREFIXES) or not self.project.startswith("partflow-s5-"):
            raise SystemExit(f"Refusing to rehearse as project {self.project!r}.")
        super().refuse_foreign_project()

    def prepare(self):
        self.secrets_dir = self.workdir / "secrets"
        self.secrets_dir.mkdir()
        write_secret_files(self.secrets_dir, ("postgres_password", *ROLE_SECRETS))
        self.backup_dir = make_backup_dir(self.workdir)
        self.env_file = self.workdir / "rehearsal.env"
        self.records = self.workdir / "records"
        self.records.mkdir()
        self.set_env(RELEASES["A"])
        self.head = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        source_a = self.workdir / "src-a"
        copy_checkout(source_a)
        self.code_head = alembic_head(source_a / "backend" / "alembic" / "versions")
        source_b = self.workdir / "src-b"
        shutil.copytree(source_a, source_b)
        (source_b / "backend" / "alembic" / "versions" / f"{NOOP_REVISION}.py").write_text(
            NOOP_TEMPLATE.format(revision=NOOP_REVISION, down=self.code_head), encoding="utf-8", newline="\n")
        source_f = self.workdir / "src-f"
        shutil.copytree(source_a, source_f)
        (source_f / "backend" / "alembic" / "versions" / f"{FINDING_REVISION}.py").write_text(
            FINDING_TEMPLATE.format(revision=FINDING_REVISION, down=self.code_head, function=FINDING_FUNCTION),
            encoding="utf-8", newline="\n")
        # F's checkout copy also carries the scripts (step A of path 3 runs there).
        shutil.copytree(REPO / "deploy" / "production", source_f / "deploy" / "production",
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        self.sources = {"A": source_a, "B": source_b, "F": source_f}
        # A': the last pre-S5 commit's production files and scripts.
        self.a_prime = self.workdir / "src-a-prime"
        self.a_prime.mkdir()
        command = ["git", "archive", "--format=tar", PRE_S5_COMMIT, *PRE_S5_PATHS]
        archive = subprocess.run(command, cwd=REPO, capture_output=True, timeout=600)
        self.evidence["commands"].append({"command": display(command), "rc": archive.returncode, "bytes": len(archive.stdout)})
        check(archive.returncode == 0, f"git archive {PRE_S5_COMMIT} failed: {archive.stderr[-500:]!r}")
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(self.a_prime, filter="data")
        self.df_shim = self.workdir / "shim"
        self.df_shim.mkdir()
        self.write_df_shim()
        self.write_id_shim()
        self.tools = self.workdir / "tools"
        self.tools.mkdir()
        wrapper = self.tools / "python3"
        wrapper.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
        os.chmod(wrapper, 0o755)
        self.evidence.update({"port": self.port, "edge_subnet": EDGE_SUBNET, "commit": self.head, "code_head": self.code_head,
                              "pre_s5_commit": PRE_S5_COMMIT, "backup_dir": posix_host_path(self.backup_dir)})

    def write_df_shim(self):
        real_df = shutil.which("df")
        check(real_df is not None, "no df on PATH")
        root = self.run(["docker", "info", "--format", "{{.DockerRootDir}}"]).stdout.strip()
        self.docker_root = root
        shim = self.df_shim / "df"
        shim.write_text(
            "#!/bin/sh\n"
            "# P16-S5 rehearsal shim (Docker Desktop): the Docker root lives in the Docker VM; measure it there.\n"
            f'for last in "$@"; do :; done\n'
            f'if [ "$last" = "{root}" ]; then\n'
            "    MSYS_NO_PATHCONV=1 exec docker run --rm --network none postgres:16.14 df -Pk /\n"
            "fi\n"
            f'exec "{Path(real_df).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
        os.chmod(shim, 0o755)

    def write_id_shim(self):
        if os.name != "nt":
            return
        real_id = shutil.which("id")
        check(real_id is not None, "no id on PATH")
        shim = self.df_shim / "id"
        shim.write_text(
            "#!/bin/sh\n"
            "# P16-S5 rehearsal shim (Docker Desktop): host-created files show as uid/gid 0 inside containers.\n"
            'case "$*" in -u | -g) echo 0; exit 0 ;; esac\n'
            f'exec "{Path(real_id).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
        os.chmod(shim, 0o755)

    def environment(self, **extra):
        env = super().environment(**extra)
        if getattr(self, "tools", None):
            if "PATH" not in extra and getattr(self, "use_shims", False):
                env["PATH"] = str(self.df_shim) + os.pathsep + env.get("PATH", "")
            env["PATH"] = str(self.tools) + os.pathsep + env.get("PATH", "")
        return env

    # -- helpers --------------------------------------------------------------

    def write_headers(self, release=None, **extra):
        return {"X-PartFlow-CSRF": "1", "Content-Type": "application/json", "X-PartFlow-Release": release or self.serving, **extra}

    def script(self, script, *arguments, env_extra=None, timeout=1800, shim=True):
        env_extra = dict(env_extra or {})
        if not shim:
            env_extra["PATH"] = os.environ.get("PATH", "")
        return self.run(["sh", Path(script).as_posix(), *arguments], timeout=timeout, check_rc=False, record_output=True,
                        env_extra=env_extra)

    def backup(self, *arguments, kind="daily"):
        extra = ["--keep-daily", "14", "--keep-weekly", "8"] if kind == "daily" else []
        result = self.script(BACKUP_SH, "--kind", kind, "--operator", "s5 rehearsal", *extra, "--env-file",
                             self.env_file.as_posix(), "--rehearsal", "--project", self.project, *arguments)
        match = re.fullmatch(r"BACKUP (\S+) (\S+)\n", result.stdout)
        self.save_log(f"backup-{kind}-{len(self.evidence['commands'])}.log", result.stdout + result.stderr)
        return result, (match.group(1) if match else None)

    def drill(self, backup, *arguments, keep=False, shim=True):
        project = f"partflow-restore-s5-{len(self.drills) + 1}-{int(time.time()) % 100000}"
        port = free_port()
        args = ["--backup", backup, "--operator", "s5 rehearsal", "--env-file", self.env_file.as_posix(),
                "--records-dir", posix_host_path(self.records), "--project", project, "--http-port", str(port),
                "--edge-subnet", DRILL_SUBNET, *arguments]
        if keep:
            args.append("--keep")
        self.drills.append(project)
        result = self.script(RESTORE_SH, *args, timeout=2400, shim=shim)
        self.save_log(f"drill-{project}.log", result.stdout + result.stderr)
        found = sorted(self.records.glob(f"*-restore-test-{backup}"), key=lambda p: p.stat().st_mtime)
        evidence = None
        if found and (found[-1] / "evidence.json").exists():
            evidence = json.loads((found[-1] / "evidence.json").read_text(encoding="utf-8"))
        return result, evidence, project, port

    def project_resources(self, project):
        label = f"label=com.docker.compose.project={project}"
        containers = self.run(["docker", "ps", "-a", "-q", "--filter", label]).stdout.split()
        volumes = self.run(["docker", "volume", "ls", "-q", "--filter", label]).stdout.split()
        return containers, volumes

    def save_log(self, name, text):
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            (self.log_dir / name).write_text(text, encoding="utf-8")

    def psql(self, sql, database=None):
        user = self.env_value("POSTGRES_USER")
        database = database or self.env_value("POSTGRES_DB")
        return self.compose("exec", "-T", "db", "psql", "-U", user, "-d", database, "-At", "-c", sql).stdout.strip()

    def production_state(self):
        containers = {}
        for service in ("db", "backend", "web"):
            ids = self.compose("ps", "-q", service).stdout.split()
            if ids:
                info = self.inspect(ids[0])
                containers[service] = {"id": info["Id"][:12], "started_at": info["State"]["StartedAt"]}
        return {"containers": containers, "revision": self.psql("select version_num from alembic_version"),
                "rows": self.psql(ROW_COUNTS_SQL)}

    def documented(self, name, block, cwd, substitutions, input_env=None):
        """Run a documented block with `sh` from a shell without PARTFLOW_*/POSTGRES_* variables."""
        text = PROJECT_GUARD.replace("<PROJECT>", self.project) + block
        for key, value in substitutions.items():
            text = text.replace(f"<{key}>", value)
        path = self.workdir / f"{name}.sh"
        path.write_text(text, encoding="utf-8", newline="\n")
        env = {"PATH": os.pathsep.join((str(self.tools), str(self.df_shim), os.environ["PATH"])), "HOME": os.environ.get("HOME", self.workdir.as_posix()),
               "COMPOSE_PROJECT_NAME": self.project}
        if os.name == "nt":
            env.update({k: os.environ[k] for k in WINDOWS_VARIABLES if k in os.environ})
        env.update(input_env or {})
        started = time.monotonic()
        result = subprocess.run(["sh", "-x", path.as_posix()], cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=1800)
        self.evidence["commands"].append({"command": f"sh -x {path.name} (cwd {cwd.name}; env -i PATH HOME COMPOSE_PROJECT_NAME)",
                                          "rc": result.returncode, "seconds": round(time.monotonic() - started, 2),
                                          "output_tail": (result.stdout + result.stderr)[-3000:]})
        self.save_log(f"{name}.log", text + "\n----- stdout -----\n" + result.stdout + "\n----- stderr -----\n" + result.stderr)
        return result

    def manifest(self, name, directory=None):
        return json.loads(((directory or self.backup_dir) / name / "manifest.json").read_text(encoding="utf-8"))

    # -- cases ----------------------------------------------------------------

    def dr1_install_and_daily(self):
        with self.case("DR-1") as observed:
            for name in ("A", "B", "F"):
                self.build(name)
            self.serving = RELEASES["A"]
            self.compose("up", "-d", "--wait", "db", timeout=300)
            self.compose("--profile", "ops", "run", "--rm", "-T", "db-roles", timeout=300)
            migrate = parse_report(self.compose("--profile", "ops", "run", "--rm", "-T", "migrate", "--no-backup-reason",
                                                "s5 rehearsal: first install", timeout=300).stdout)
            check(migrate.get("result") == "upgraded", f"first install migrate: {migrate.get('result')}")
            self.compose("up", "-d", "backend", "web", timeout=300, env_extra={"PARTFLOW_BACKEND_WORKERS": "1"})
            self.wait_for(lambda a, b: getattr(a, "status", None) == 200, 120, "health 200 on A")
            self.seed(observed)
            started = time.monotonic()
            result, name = self.backup()
            observed["backup"] = {"rc": result.returncode, "name": name, "seconds": round(time.monotonic() - started, 2),
                                  "progress": [line for line in result.stderr.splitlines() if re.match(r"backup: \w+ ok", line)]}
            check(result.returncode == 0 and name, f"backup.sh daily exit {result.returncode}: {result.stderr[-800:]}")
            self.daily = name
            sums = self.run(["sh", "-c", f'cd "{posix_host_path(self.backup_dir / name)}" && sha256sum -c SHA256SUMS'])
            observed["sha256sum"] = sums.stdout.strip().splitlines()
            manifest = self.manifest(name)
            observed["manifest"] = {k: manifest.get(k) for k in ("name", "kind", "alembic_revision", "dump_started_at", "release", "dump", "database")}
            check(manifest["alembic_revision"] == self.code_head, "manifest revision is not A's head")
            check(manifest["dump"]["tables_checked"] is True and manifest["dump"]["table_data_entries"] == 29, "table data entries")
            check(manifest["dump"]["extra_tables"] == [], "extra tables")
            check(manifest["release"]["tag"] == RELEASES["A"], "manifest release is not A")
            check(sorted(p.name for p in (self.backup_dir / name).iterdir()) ==
                  ["SHA256SUMS", "manifest.json", "partflow.dump", "partflow.dump.list"], "backup directory content")

    def seed(self, observed):
        """Synthetic data through web: the administrator, a Part Number with an image, imported Work Orders."""
        status = self.client.request("GET", "/api/setup")
        setup = status.json()
        check(status.status == 200 and setup.get("open") is True, "setup is not open")
        tokens = TOKEN_PATTERN.findall(self.compose("logs", "--no-log-prefix", "backend").stdout)
        check(len(tokens) == 1, "expected one announced setup token")
        role = next((r for r in setup["eligible_roles"] if r["name"] == "Administrator"), setup["eligible_roles"][0])
        self.admin_login = "s5-rehearsal-admin"
        self.admin_password = os.urandom(18).hex()
        body = json.dumps({"setup_token": tokens[0], "login_name": self.admin_login, "display_name": "S5 Rehearsal",
                           "role_id": role["id"], "password": self.admin_password}).encode("utf-8")
        answer = self.client.request("POST", "/api/setup/administrator", headers=self.write_headers(), body=body)
        check(answer.status == 201, f"setup answered {answer.status}: {answer.excerpt()}")
        self.client.remember_cookie(answer)
        self.user = answer.json()["user"]
        grant = self.client.request("PATCH", f"/api/roles/{self.user['role_id']}", headers=self.write_headers(),
                                    body=json.dumps({"grant_permissions": ["MANAGE_WORK_ORDERS", "MANAGE_PART_NUMBER_MASTER"]}).encode())
        check(grant.status == 200, f"grant answered {grant.status}: {grant.excerpt()}")
        created = self.client.request("POST", "/api/part-numbers", headers=self.write_headers(),
                                      body=json.dumps({"part_number": PN, "name": "S5 rehearsal part"}).encode())
        check(created.status == 201, f"create PN answered {created.status}: {created.excerpt()}")
        image = self.client.request("PUT", f"/api/part-numbers/image?number={PN}",
                                    headers={**self.write_headers(), "Content-Type": "image/png"}, body=PNG)
        check(image.status == 200, f"image upload answered {image.status}: {image.excerpt()}")
        self.image_sha256 = hashlib.sha256(PNG).hexdigest()
        rows = "".join(f"S5DR-{i:04d},S5-DR-PN-{i:04d},{i},," + "\r\n" for i in range(1, 51))
        body = (IMPORT_HEADER + rows).encode("utf-8")
        headers = {**self.write_headers(), "Content-Type": "text/csv"}
        preview = self.client.request("POST", "/api/work-orders/import/preview", headers=headers, body=body, timeout=200)
        check(preview.status == 200, f"preview answered {preview.status}: {preview.excerpt()}")
        report = preview.json()
        commit_headers = {**headers, "X-PartFlow-Import-Check": report["check_token"]}
        committed = self.client.request("POST", "/api/work-orders/import", headers=commit_headers, body=body, timeout=200)
        check(committed.status == 200, f"import answered {committed.status}: {committed.excerpt()}")
        observed["seed"] = {"part_number": PN, "image_sha256": self.image_sha256, "import": committed.json().get("summary")}

    def read_image(self, port):
        client = Client(port)
        answer = client.request("GET", f"/api/part-numbers/image?number={PN}", with_cookie=False)
        return answer.status, hashlib.sha256(answer.body).hexdigest()

    def dr2_drill(self):
        with self.case("DR-2a (A4 on this host: no shim)") as observed:
            result, evidence, project, _ = self.drill(self.daily, shim=False)
            observed.update({"rc": result.returncode, "stderr_tail": result.stderr[-600:], "docker_root": self.docker_root})
            if result.returncode == 0:
                observed["note"] = "the host can df the Docker root: no shim needed"
                self.teardown_drill(project)
            else:
                check(result.returncode == 2 and "could not be read (df -Pk)" in result.stderr,
                      "restore-test.sh did not stop could_not_run on the unreadable Docker root")
                check(self.project_resources(project) == ([], []), "a project was created")
        with self.case("DR-2") as observed:
            before = self.production_state()
            result, evidence, project, port = self.drill(self.daily, keep=True)
            observed.update({"rc": result.returncode, "evidence": evidence})
            try:
                check(result.returncode == 0 and evidence and evidence["outcome"] == "passed", f"drill exit {result.returncode}: {result.stderr[-800:]}")
                check(evidence["reconcile"]["checks"] == {"h": "pass", "j": "pass"}, "checks h and j")
                status, digest = self.read_image(port)
                observed["image"] = {"status": status, "sha256": digest}
                check(status == 200 and digest == self.image_sha256, "the uploaded image was not read back from the drill")
            finally:
                self.teardown_drill(project)
            observed["left"] = self.project_resources(project)
            check(observed["left"] == ([], []), "the drill project left containers or volumes")
            after = self.production_state()
            observed["production_unchanged"] = before == after
            check(before == after, f"the production project changed: {before} -> {after}")
            self.dr2_server = evidence["server"]

    def teardown_drill(self, project):
        check(project.startswith("partflow-restore-s5-"), f"refusing to tear down {project}")
        self.run(["docker", "compose", "-p", project, "down", "-v", "--remove-orphans"], check_rc=False, timeout=600)

    def dr3_candidate_server(self):
        with self.case("DR-3") as observed:
            present = self.run(["docker", "image", "inspect", CANDIDATE_DB_IMAGE], check_rc=False).returncode == 0
            if not present:
                self.pulled.append(CANDIDATE_DB_IMAGE)
            result, evidence, project, _ = self.drill(self.daily, "--db-image", CANDIDATE_DB_IMAGE)
            observed.update({"rc": result.returncode, "image": CANDIDATE_DB_IMAGE, "pulled": not present,
                             "server": (evidence or {}).get("server"), "dr2_server": getattr(self, "dr2_server", None),
                             "outcome": (evidence or {}).get("outcome"), "checks": ((evidence or {}).get("reconcile") or {}).get("checks")})
            check(result.returncode == 0 and evidence["outcome"] == "passed", f"drill exit {result.returncode}: {result.stderr[-800:]}")
            check(self.project_resources(project) == ([], []), "the drill project was not torn down")
            check(evidence["server"]["collation_version_actual"] != (self.dr2_server or {}).get("collation_version_actual"),
                  "the candidate server reports the same collation version")

    def dr4_path3(self):
        with self.case("DR-4") as observed:
            result, record = self.release("F", backup=())
            observed["release"] = {"rc": result.returncode, "outcome": (record or {}).get("outcome"),
                                   "steps": [(s["name"], s["exit_code"], s["started_at"], s["finished_at"]) for s in (record or {}).get("steps", [])],
                                   "backup": (record or {}).get("backup"), "freeze_completed_at": (record or {}).get("freeze_completed_at")}
            check(result.returncode == 3 and record["outcome"] == "stopped_frozen", f"release.sh F exit {result.returncode}")
            check(record["writes_reopened_at"] is None, "writes were reopened")
            check(record["backup"]["taken_by"] == "release.sh" and record["backup"]["verified"] is True, "backup not taken/verified")
            directory = sorted(self.records.glob(f"*-{RELEASES['F']}/record.json"))[-1].parent
            post = json.loads((directory / "post-reconcile.json").read_text(encoding="utf-8"))
            failing = [(c["id"], [f["code"] for f in c["findings"]]) for c in post["checks"] if c["status"] != "pass" and c["status"] != "not_applicable"]
            observed["post_findings"] = failing
            check([c for c, _ in failing] == ["h"], f"the post reconcile did not find exactly check (h): {failing}")
            manifest = self.manifest(record["backup"]["name"])
            check(manifest["dump_started_at"] >= record["freeze_completed_at"], "the dump started before the freeze")
            steps = {s["name"]: s for s in record["steps"]}
            freeze_start = datetime.datetime.fromisoformat(steps["freeze"]["started_at"].replace("Z", "+00:00"))
            migrate_start = datetime.datetime.fromisoformat(steps["migrate"]["started_at"].replace("Z", "+00:00"))
            observed["freeze_to_migrate_seconds"] = (migrate_start - freeze_start).total_seconds()
            observed["backup_duration_ms"] = record["backup"]["duration_ms"]
            # Path 3, exactly as documented. Both checkouts get the stack's env file as .env.production.
            source_f = self.sources["F"]
            shutil.copyfile(self.env_file, source_f / ".env.production")
            rec = posix_host_path(directory)
            a = self.documented("dr4-step-a", STEP_A, source_f, {"REC": rec, "CAND": RELEASES["F"], "PREV": RELEASES["A"]})
            observed["step_a"] = {"rc": a.returncode, "tail": a.stdout[-1200:]}
            check(a.returncode == 0, f"step A exit {a.returncode}: {a.stderr[-1500:]}")
            check("writes were reopened" not in a.stdout, "step A says path 4")
            check(": OK" in a.stdout and '"result": "verified"' in a.stdout, "sha256sum -c or backup-verify did not pass")
            bpath = record["backup"]["path"]
            shutil.copyfile(self.env_file, self.a_prime / ".env.production")
            b1 = self.documented("dr4-step-b1", STEP_B1, self.a_prime, {"BPATH": bpath, "REC": rec, "PREV": RELEASES["A"]})
            observed["step_b1"] = {"rc": b1.returncode, "tail": b1.stdout[-1500:]}
            check(b1.returncode == 0, f"step B1 exit {b1.returncode}: {b1.stderr[-1500:]}")
            check("not enough space" not in b1.stdout, "space check failed")
            check("reconcile exit 0" in b1.stdout, "rollback reconcile did not exit 0")
            check('"state": "current"' in b1.stdout, "revision is not current")
            names = re.search(r"NEWDB=(\S+) OLDDB=(\S+)", b1.stdout)
            new_db, old_db = names.group(1), names.group(2)
            reconcile = json.loads((directory / "rollback-reconcile.json").read_text(encoding="utf-8"))
            check(not [c for c in reconcile["checks"] if c["status"] == "fail"], "the deliberate finding is still present")
            # The owner's approval, recorded before backend starts on the restored database (test values).
            rollback = {"environment": "rehearsal", "release": RELEASES["A"], "previous_release": RELEASES["F"],
                        "operator": "s5 rehearsal", "approved_by": "s5 rehearsal owner (test)",
                        "approved_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "reason": "DR-4 rehearsal of rollback path 3", "backup": record["backup"]["name"],
                        "new_database": new_db, "old_database": old_db,
                        "reconcile": {"pre": "pre-reconcile.json", "rollback": "rollback-reconcile.json"}}
            (directory / "rollback.json").write_text(json.dumps(rollback, indent=2) + "\n", encoding="utf-8")
            b2 = self.documented("dr4-step-b2", STEP_B2, self.a_prime, {"PREV": RELEASES["A"]})
            observed["step_b2"] = {"rc": b2.returncode, "tail": b2.stdout[-1500:]}
            check(b2.returncode == 0, f"step B2 exit {b2.returncode}: {b2.stderr[-1500:]}")
            check("FAIL" not in b2.stdout, "smoke.sh A failed")
            # The stack now runs A on the restored database: the rehearsal's env file follows.
            shutil.copyfile(self.a_prime / ".env.production", self.env_file)
            self.serving = RELEASES["A"]
            _, body = self.health()
            observed["health"] = body
            check(body.get("release") == RELEASES["A"] and body.get("schema") == "current", "health is not A/current")
            check(self.env_value("POSTGRES_DB") == new_db, "the env file does not name the restored database")
            old_revision = self.psql("select version_num from alembic_version", database=old_db)
            observed["migrated_database"] = {"name": old_db, "revision": old_revision}
            check(old_revision == FINDING_REVISION, "the migrated database was not preserved")

    def dr4b_atomic(self):
        with self.case("DR-4b") as observed:
            before = self.production_state()
            target = f"{self.env_value('POSTGRES_DB')}_x"
            user = self.env_value("POSTGRES_USER")
            self.compose("exec", "-T", "db", "createdb", "-U", user, "--template=template0", target)
            self.compose("exec", "-T", "db", "psql", "-U", user, "-d", target, "-c", "CREATE TABLE public.part_movements (x int)")
            dump = self.backup_dir / self.daily / "partflow.dump"
            with dump.open("rb") as stdin:
                restore = subprocess.run(
                    ["docker", "compose", "-p", self.project, "-f", str(REPO / "compose.production.yaml"), "--env-file",
                     str(self.env_file), "exec", "-T", "db", "sh", "-c",
                     'pg_restore -U "$POSTGRES_USER" -d "$1" --single-transaction --exit-on-error --no-owner --no-privileges',
                     "sh", target], stdin=stdin, capture_output=True, env=self.environment(), timeout=600)
            tables = self.psql("select string_agg(tablename, ',' order by tablename) from pg_tables where schemaname = 'public'",
                               database=target)
            observed.update({"restore_rc": restore.returncode, "stderr_tail": restore.stderr[-500:].decode("utf-8", "replace"),
                             "tables_after": tables})
            check(restore.returncode != 0, "the conflicting restore succeeded")
            check(tables == "part_movements", f"the target holds restored objects: {tables}")
            self.compose("exec", "-T", "db", "sh", "-c", 'dropdb -U "$POSTGRES_USER" "$1"', "sh", target)
            exists = self.psql(f"select count(*) from pg_database where datname = '{target}'")
            check(exists == "0", "dropdb did not remove the target")
            after = self.production_state()
            check(before == after, "the live database or containers changed")

    def dr5_stale(self):
        with self.case("DR-5") as observed:
            before = self.psql("select version_num from alembic_version")
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ids = self.run(["sh", "-c", "echo $(id -u):$(id -g)"]).stdout.strip()
            observed["user"] = ids
            result = self.compose("--profile", "ops", "run", "--rm", "-T", "--user", ids, "migrate", "--pre-release-backup",
                                  self.daily, "--backup-not-before", now, release=RELEASES["B"], check_rc=False, timeout=300)
            report = parse_report(result.stdout)
            observed.update({"rc": result.returncode, "result": report.get("result"), "error": report.get("error")})
            check(result.returncode == 1 and (report.get("error") or {}).get("code") == "backup_stale", "not refused backup_stale")
            check(self.psql("select version_num from alembic_version") == before, "the revision changed")

    def dr6_tamper(self):
        with self.case("DR-6") as observed:
            parent = self.workdir / "second"
            parent.mkdir()
            second = make_backup_dir(parent)
            shutil.copytree(self.backup_dir / self.daily, second / self.daily)
            dump = second / self.daily / "partflow.dump"
            data = bytearray(dump.read_bytes())
            data[len(data) // 2] ^= 0x01
            dump.write_bytes(bytes(data))
            ids = self.run(["sh", "-c", "echo $(id -u):$(id -g)"]).stdout.strip()
            shell = {"PARTFLOW_BACKUP_DIR": posix_host_path(second)}
            verify = self.compose("--profile", "ops", "run", "--rm", "--no-deps", "-T", "--user", ids, "backup-tools",
                                  "backup-verify", self.daily, release=RELEASES["B"], env_extra=shell, check_rc=False, timeout=300)
            migrate = self.compose("--profile", "ops", "run", "--rm", "-T", "--user", ids, "migrate", "--pre-release-backup",
                                   self.daily, release=RELEASES["B"], env_extra=shell, check_rc=False, timeout=300)
            original = self.compose("--profile", "ops", "run", "--rm", "--no-deps", "-T", "--user", ids, "backup-tools",
                                    "backup-verify", self.daily, release=RELEASES["B"], check_rc=False, timeout=300)
            observed.update({"verify_rc": verify.returncode, "verify_error": parse_report(verify.stdout).get("error"),
                             "migrate_rc": migrate.returncode, "migrate_error": parse_report(migrate.stdout).get("error"),
                             "original_rc": original.returncode})
            check(verify.returncode == 1, "the tampered backup verified")
            check((parse_report(migrate.stdout).get("error") or {}).get("code") == "backup_invalid", "migrate did not refuse backup_invalid")
            check(original.returncode == 0, "the original backup no longer verifies")

    def dr7_backup_during_writes(self):
        with self.case("DR-7") as observed:
            stop = threading.Event()
            writes = {"ok": 0, "other": []}

            def loop():
                index = 0
                while not stop.is_set():
                    index += 1
                    body = json.dumps({"name": f"S5 rehearsal write {index}"}).encode()
                    try:
                        answer = self.client.request("PATCH", f"/api/part-numbers?number={PN}", headers=self.write_headers(), body=body)
                        if answer.status == 200:
                            writes["ok"] += 1
                        else:
                            writes["other"].append(answer.status)
                    except OSError as exc:
                        writes["other"].append(type(exc).__name__)

            thread = threading.Thread(target=loop, daemon=True)
            thread.start()
            try:
                result, name = self.backup("--reason", "DR-7 backup during writes", kind="manual")
            finally:
                stop.set()
                thread.join(timeout=60)
            observed.update({"rc": result.returncode, "name": name, "writes_ok": writes["ok"], "writes_other": writes["other"][:20]})
            check(result.returncode == 0 and name, f"backup.sh manual exit {result.returncode}: {result.stderr[-800:]}")
            check(writes["ok"] > 0, "no write ran during the backup")
            drill, evidence, project, _ = self.drill(name)
            observed["drill"] = {"rc": drill.returncode, "outcome": (evidence or {}).get("outcome"),
                                 "reconcile": (evidence or {}).get("reconcile"), "timings_ms": (evidence or {}).get("timings_ms")}
            check(drill.returncode == 0 and evidence["outcome"] == "passed" and evidence["reconcile"]["exit_code"] == 0, "the drill did not pass")
            check(self.project_resources(project) == ([], []), "the drill project was not torn down")

    def dr8_concurrent(self):
        with self.case("DR-8") as observed:
            command = ["sh", BACKUP_SH.as_posix(), "--kind", "manual", "--reason", "DR-8 concurrency", "--operator", "s5 rehearsal",
                       "--env-file", self.env_file.as_posix(), "--rehearsal", "--project", self.project]
            env = self.environment()
            first = subprocess.Popen(command, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", errors="replace")
            second = subprocess.Popen(command, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      encoding="utf-8", errors="replace")
            outputs = [p.communicate(timeout=1800) for p in (first, second)]
            codes = sorted([first.returncode, second.returncode])
            observed.update({"exit_codes": codes, "stderr": [o[1][-400:] for o in outputs]})
            self.evidence["commands"].append({"command": "2 x " + display(command), "rc": codes})
            check(codes == [0, 1], f"exit codes {codes}")
            refused = [o for p, o in zip((first, second), outputs) if p.returncode == 1][0]
            check("a backup is running" in refused[1], "the refusal is not backup_running")
            check(not (self.backup_dir / ".backup.lock").exists(), "the lock was left")

    def dr4c_completed_release(self):
        with self.case("DR-4c (path-4 example)") as observed:
            result, record = self.release("B", backup=())
            observed["release"] = {"rc": result.returncode, "outcome": (record or {}).get("outcome"),
                                   "backup": (record or {}).get("backup"), "freeze_completed_at": (record or {}).get("freeze_completed_at"),
                                   "writes_reopened_at": (record or {}).get("writes_reopened_at")}
            check(result.returncode == 0 and record["outcome"] == "completed", f"release.sh B exit {result.returncode}: {result.stderr[-800:]}")
            check(record["backup"]["taken_by"] == "release.sh" and record["backup"]["verified"] is True, "backup not verified")
            check(record["writes_reopened_at"], "writes_reopened_at is not set")
            directory = sorted(self.records.glob(f"*-{RELEASES['B']}/record.json"))[-1].parent
            gate = subprocess.run(["python", "-c", 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["writes_reopened_at"] is not None)',
                                   str(directory / "record.json")])
            observed["path3_precondition_rc"] = gate.returncode
            check(gate.returncode == 1, "the path-3 precondition does not send a completed release to path 4")
            self.serving = RELEASES["B"]

    def dr9_second_host(self):
        self.evidence["cases"]["DR-9"] = {"status": "not_run", "observed": {"reason": "no VPS-class host available"}}

    def run_cases(self):
        self.dr1_install_and_daily()
        if self.evidence["cases"]["DR-1"]["status"] != "pass":
            raise CaseFailure("DR-1 failed: the later cases need the installed stack and its daily backup")
        self.dr2_drill()
        self.dr3_candidate_server()
        self.dr4_path3()
        self.dr4b_atomic()
        self.dr5_stale()
        self.dr6_tamper()
        self.dr7_backup_during_writes()
        self.dr8_concurrent()
        self.dr4c_completed_release()
        self.dr9_second_host()

    # -- teardown -------------------------------------------------------------

    def teardown(self):
        if not self.args.keep:
            for project in self.drills:
                containers, volumes = self.project_resources(project)
                if containers or volumes:
                    self.teardown_drill(project)
        super().teardown()
        if not self.args.keep:
            for image in self.pulled:
                self.run(["docker", "image", "rm", image], check_rc=False)


def main(argv):
    parser = argparse.ArgumentParser(description="P16-S5 backup rehearsal (throwaway Compose projects).")
    parser.add_argument("--evidence", required=True, help="path of the evidence JSON to write")
    parser.add_argument("--log-dir", help="directory for the step logs")
    parser.add_argument("--keep", action="store_true", help="leave the stacks and images (prints the teardown commands)")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f"Compose project name (default {DEFAULT_PROJECT})")
    args = parser.parse_args(argv)
    evidence = {
        "slice": "P16-S5",
        "project": args.project,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "releases": dict(RELEASES),
        "commands": [],
        "cases": {},
    }
    rehearsal = BackupRehearsal(args, evidence)
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
