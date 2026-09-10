# PartFlow NAS Admin v2

> English is the source of truth for this tool guide. [Vietnamese](./SYNOLOGY_ADMIN.vi.md).
> Version: **2.2.0**. Prepared: **2026-09-09**.
> Repository reference: `CDSemi/part-flow@8d358eea0582b2e910df60569ad9865fd78f9d98`.
> Scope: the existing, restricted **Synology staging** stack, not production.
> This package does not commit, push, publish a release, or schedule a task by itself.

## 1. Choose a deployment policy

Use manual commit updates for active staging work. Publish a release when selecting
an identifiable build for a test session or deployment, not for every small commit.
Use published releases for unattended updates. Never automatically track `main` on
a production instance.

At the inspected repository revision, `v0.1.0-alpha.1` is a **pre-release** and points
to `d277f8e53a7ca79e0211c211a344dce60e8c7d7f`. The branch tip is newer. GitHub's
`releases/latest` endpoint excludes pre-releases, so a stable-only checker can
correctly report that no eligible release exists. It does not fall back to `main`.

| Channel | Selection |
| --- | --- |
| `stable` | GitHub's latest published, non-draft, non-prerelease release |
| `prerelease` | Latest publication date across non-draft stable releases and pre-releases |

The script resolves the selected branch/tag to a full commit SHA, then checks out
that SHA. It never treats a release's `target_commitish: main` as its pinned code.
A previously observed release tag changing SHA is refused. Do not reuse tags.

## 2. What is included

| File | Purpose |
| --- | --- |
| `pf.sh` | Root entry point and host Python/runtime discovery |
| `compose.nas.yaml` | NAS-specific Compose definition kept at repository root |
| `deploy/synology/pf-admin.py` | Update, checkpoint, rollback, reset and release-check workflows |
| `deploy/synology/backup.sh` | Noninteractive scheduled/manual checkpoint wrapper |
| `deploy/synology/release-check.sh` | Scheduler wrapper; check-only unless explicitly enabled |
| `deploy/synology/pf-config.example.json` | Non-secret administration settings |
| `deploy/synology/nas.env.example` | Example NAS environment values |
| `deploy/synology/tests/test_pf_admin.py` | Offline controller tests |
| `deploy/synology/TEST_REPORT.md` | Validation record and limitations |
| `deploy/synology/.gitignore` | Ignores local `pf-config.json` and Python cache files |
| `docs/deployment/SYNOLOGY_ADMIN.md`, `SYNOLOGY_ADMIN.vi.md` | Administration documentation |

The lifecycle logic uses Python's standard library, rather than shell parsing of
JSON or executing backup metadata as shell code. No pip packages are needed.
The root entry point, NAS Compose file, and `deploy/synology/` controller/configuration
tree are deliberately **not self-updated** while a lifecycle command is running. Repository
documentation and normal application source follow the selected revision.

## 3. Requirements and installation

This package upgrades an **already initialized staging stack** from the earlier
NAS guide. The database must be running, migrated, and use PostgreSQL 16. There
must be one existing `db`, `backend`, and `frontend` container in the configured
Compose project. Initial installation still follows the NAS deployment guide.

The NAS host needs Git, Python **3.9 or newer**, Docker and a working Compose CLI.
Prefer a maintained Python package supported for the exact NAS/DSM model. Do not
replace DSM's system interpreter. The application containers still use their own
runtimes; this additional host Python is only for administration. No jq is needed.

Check over SSH:

```sh
python3 --version
git --version
sudo docker version
sudo docker compose version
# If Compose v2 is absent:
sudo docker-compose version
```

`pf.sh` tries common Python executable names, the usual Synology Python 3.9
package path, and common SynoCommunity `python310`–`python314` package paths.
An explicit executable can be selected without changing the script:

```sh
sudo env PF_PYTHON=/absolute/path/to/python3 sh ./pf.sh doctor
```

Replace the path with a real, verified executable. Set the same override in the
DSM task if necessary. Do not install an unsupported NAS package just to bypass a
failed prerequisite check.

### Install without resetting anything

Keep the existing `.env`, `compose.nas.yaml`, source, project name and Docker volumes.
Save a copy of the old administration files first. Example from SSH:

```sh
cd /volume1/docker/partflow
saved="backups/admin-tools-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$saved"
chmod 700 "$saved"
for path in repo/pf.sh repo/compose.nas.yaml repo/deploy/synology; do
    if [ -e "$path" ]; then
        cp -Rp "$path" "$saved/"
    fi
done
```

Extract/upload this package **into the repository root while preserving its directory
structure**. Do not flatten the files. The resulting layout is:

```text
repo/
├── pf.sh
├── compose.nas.yaml
├── deploy/
│   └── synology/
│       ├── pf-admin.py
│       ├── backup.sh
│       ├── release-check.sh
│       ├── pf-config.example.json
│       ├── nas.env.example
│       ├── TEST_REPORT.md
│       ├── .gitignore
│       └── tests/
│           └── test_pf_admin.py
└── docs/
    └── deployment/
        ├── SYNOLOGY_ADMIN.md
        └── SYNOLOGY_ADMIN.vi.md
```

Keep the existing root `.env`. Do not replace a locally customized
`compose.nas.yaml` with the reference copy without reviewing the differences. The tests
belong to this controller, not the application's database integration suite; run them on
a workstation or separate test copy.

Migrate the old runtime configuration if this NAS already used Admin v2, then remove the
obsolete root-level duplicates:

```sh
cd /volume1/docker/partflow/repo
if [ -f pf-config.json ] && [ ! -f deploy/synology/pf-config.json ]; then
  cp -p pf-config.json deploy/synology/pf-config.json
fi
test -f deploy/synology/pf-config.json || \
  cp deploy/synology/pf-config.example.json deploy/synology/pf-config.json
chmod 600 .env deploy/synology/pf-config.json

rm -f pf-admin.py backup.sh release-check.sh pf-config.json \
  pf-config.example.json nas.env.example PF_ADMIN_GUIDE.md \
  PF_ADMIN_GUIDE.vi.md TEST_REPORT.md
rm -rf pf-admin-tests

sudo sh ./pf.sh doctor
sudo sh ./pf.sh status
sudo sh ./pf.sh backup
```

`pf-config.example.json` allows the DSM `administrators` group to read backup
artifacts over SMB by default:

```text
"backup_read_group": "administrators"
```

If the NAS uses a dedicated administration group, change this value in
`deploy/synology/pf-config.json` before running `doctor`. The group must exist on
DSM. Do not use a broad group such as `users` merely to avoid permission errors.

The controller intentionally refuses the old root-level Admin v2 layout so stale duplicate
scripts/configuration cannot be used accidentally. `deploy/synology/pf-config.json` is
deployment-local and is ignored by the included nested `.gitignore`. `DEPLOYED_SOURCE.txt`
is also runtime deployment state; add it to the repository root `.gitignore` before
committing this structure.

Keep `project` as `partflow-staging` when upgrading the old bundle. Changing this
name selects a different Compose deployment and potentially different volumes.
`DEPLOYED_SOURCE.txt` must contain the actual 40-character source SHA for a ZIP
installation; it must agree with Git HEAD for a Git checkout. Do not invent a SHA.

The following directories are generated **outside the replaceable source tree**:

```text
partflow/
  repo/                                    application checkout + local controls
  .pf-state-partflow-staging/               lock, operation journal, image selection
  backups/
    revisions/
      partflow-staging/
        <backup-id>/
          source.tar.gz
          database.dump
          database.list
          manifest.json
          manifest.sha256
```

`.pf-state-partflow-staging/` remains private at `0700` and is not intended for
SMB browsing. The revision checkpoint tree for this project is instead assigned
to `backup_read_group` with:

```text
Directory: 0750
File:      0640
```

Trusted DSM administrators can therefore browse and copy checkpoints over SMB
without receiving POSIX write permission. Admin v2.2 also repairs permissions on
existing v2.x checkpoints under `backups/revisions/<project>/` when the
controller starts, so historical checkpoints do not require manual `chmod`.
The source archive includes `.env`; only grant `backup_read_group` to a trusted
administrative group allowed to read secrets and database backups.

## 4. Commands at a glance

Run commands from `repo/`, using the same administrator/privilege approach each time.

| Command | Effect |
| --- | --- |
| `sudo sh ./pf.sh doctor` | Check tools, Compose configuration and source-volume free space |
| `sudo sh ./pf.sh status` | Show source, containers, database revisions and any incomplete operation |
| `sudo sh ./pf.sh update --latest` | Manually deploy the latest configured branch commit |
| `sudo sh ./pf.sh update --commit FULL_SHA` | Manually deploy a specific commit |
| `sudo sh ./pf.sh update --release TAG` | Manually deploy an explicit published release |
| `sudo sh ./pf.sh update --release latest --channel prerelease` | Select the newest eligible staging release |
| `sudo sh ./pf.sh backups --page 1` | List 10 checkpoints, newest first |
| `sudo sh ./pf.sh rollback` | Interactive paginated checkpoint selection |
| `sudo sh ./pf.sh rollback BACKUP_ID` | Select an exact checkpoint; retain current database data |
| `sudo sh ./pf.sh rollback BACKUP_ID --restore-db` | Restore application plus that checkpoint's database |
| `sudo sh ./pf.sh reset-db` | Switch the active instance to a clean migrated database |
| `sudo sh ./pf.sh backup` | Create and restore-test a checkpoint without stopping the application |
| `sudo sh ./pf.sh release-check` | Check the release feed; no application/database update |
| `sudo sh ./pf.sh release-check --apply` | Apply only if auto-update is enabled and every automated gate passes |
| `sudo sh ./pf.sh resume` | Resume an unchanged deployment after an early, pre-change failure |

Replace `FULL_SHA`, `TAG`, and `BACKUP_ID` with actual values. `update` without a
selector defaults to the release channel, not to the moving branch tip. Existing
Compose commands such as `ps`, `logs`, `exec`, `build`, `up`, and `stop` still work.
The project and Compose file are fixed. Volume-deleting `down`/`rm` options are
blocked. Direct Docker access can bypass this tool; it is not an authorization layer.

## 5. Manual update

For everyday staging changes:

```sh
sudo sh ./pf.sh update --latest
```

The workflow resolves a fixed SHA, verifies the latest `ci.yml` push run for that
exact SHA, clones a new checkout, and builds uniquely tagged candidate images
without replacing the running images. It then asks for:

```text
UPDATE <12-character-target-SHA>
```

After confirmation it stops frontend/backend, checkpoints the old source and
database, performs a full restore test in a separate temporary database, applies
approved migrations when needed, replaces the application source, and starts the
selected images. Backend health and database revision are checked before frontend
startup. The frontend's `/api/health` is checked afterward.

Local `.env`, `compose.nas.yaml`, root `pf.sh`, and the complete
`deploy/synology/` controller/configuration tree are preserved. Repository documentation
under `docs/` follows the selected source revision. Other local application-source changes
are archived, not merged into the new checkout.
The source replacement is **not an atomic directory swap**. It happens while the
application is stopped, with a persistent journal. Do not edit/upload the source
or run direct Compose commands concurrently with a managed operation.

A ZIP installation can be converted to the first managed Git checkout by a manual
update, even when the selected SHA is the same. Unattended updates require that
managed checkout and a clean application source tree.

### When migrations change

Review the migration and recovery plan, then explicitly allow it:

```sh
sudo sh ./pf.sh update --latest --allow-migrations
# Or select the exact published release:
sudo sh ./pf.sh update --release TAG --allow-migrations
```

The new migration is first rehearsed against a restored temporary database. Only
a successful rehearsal allows the live migration. Existing migration files being
modified or deleted are refused even with this flag. Multiple Alembic heads and
an uninitialized/inconsistent current schema require manual intervention.

`--skip-ci` is an explicit **manual staging exception**, not a successful CI result.
It is never used by scheduled updates. No command in this tool runs `alembic downgrade`.

## 6. Checkpoints and rollback

Every managed update, reset, and rollback creates a checkpoint before changing
active source/data. Checkpoints include actual source files, a custom PostgreSQL
dump, Alembic revisions, PostgreSQL major version, source SHA, checksums, and local
retained image references/IDs. A temporary database is fully restored with
`pg_restore --exit-on-error`, and its Alembic revisions are compared with the dump's
recorded source database. This is more than an archive-list check, but it is **not**
a full application or quantity-reconciliation test.

The source archive excludes `.git`, virtual environments, dependency directories,
and caches. It **includes `.env` and local administration files**: protect the whole
checkpoint as a secret. Metadata is JSON and is never sourced as executable shell.
Checksums detect accidental corruption; they are not a signature against an attacker
who can rewrite both files and hashes.

### Selection

```sh
sudo sh ./pf.sh rollback
```

The menu lists newest first, 10 entries per page. Use `n`, `p`, `q`, or a displayed
number. Direct selection skips the menu, not the confirmation:

```sh
sudo sh ./pf.sh rollback BACKUP_ID
```

A full source SHA is accepted only if it identifies exactly one checkpoint; otherwise
use the checkpoint ID. Incomplete/unverified checkpoints cannot be source rollback
targets. Legacy dump-only backups remain untouched but are not source revision entries.

### Code-only rollback: default

Current data is kept. The current migration file fingerprint and live Alembic
revisions must match the selected checkpoint. Both the checkpoint and retained
images are verified first. Confirmation is `ROLLBACK BACKUP_ID`.

These checks are a conservative structural gate, **not proof of business-semantic
compatibility**. A matching Alembic head alone cannot prove an older app understands
all newer data. Review semantic changes before approving the command.

### Application and database rollback: explicit

```sh
sudo sh ./pf.sh rollback BACKUP_ID --restore-db
```

Confirmation is:

```text
RESTORE <current-database-name> <backup-id>
```

The current source/database are checkpointed again. The selected dump is restored
into a new database and checked. The old active database and the prepared database
are then renamed in one transaction on the PostgreSQL maintenance database. The old
active copy is retained as `pf_keep_<timestamp>_<suffix>` with new connections disabled.
The target application's retained images are activated against the restored database.

**Newer writes are no longer present in the active application after this operation.**
They remain in the new safety checkpoint and the retained database, not automatically
merged into the restored history. Do not manually edit immutable Movement history to
combine them. Retention is protection, not an automatic reconciliation solution.

A missing/pruned retained image makes rollback fail closed rather than rebuilding a
potentially different image from mutable base tags. Source archives do not contain
image layers. These checkpoints are not by themselves a full off-NAS disaster-recovery
bundle. Preserve/export the retained images separately before relying on recovery on
a different host. This package does not automate registry publication or image export.

## 7. Reset staging data

```sh
sudo sh ./pf.sh reset-db
```

The required text uses the real database name, for example:

```text
RESET partflow_staging
```

There is no `--yes` bypass. Reset and rollback require an interactive terminal and
must not be placed in Task Scheduler.

Reset does **not** delete the Docker volume or issue ad hoc DELETE/TRUNCATE commands.
It pauses application writes, creates/restores a verified checkpoint, creates a new
empty database, runs the current image's migrations against it, and switches database
names in one transaction. The previous database is retained with connections disabled.
The application keeps the same configured database name and URL.

All user-created data and master/environment configuration disappear from the active
instance. Migration-created defaults can still exist. Reconfigure Departments, Areas,
Operations, Machines and Scan Stations as needed. Existing external database sessions
cause the switch to be refused; the script does not forcibly terminate unrelated users.

**For go-live, prefer a separate production environment/database and clean master-data
setup.** Clearing test data does not provide authorization, production web servers,
secret handling, monitoring, backup retention, or production approval. Mutating lifecycle
commands in this version deliberately reject `environment` other than `staging`.
Do not relabel a real production database as staging to bypass this restriction.

## 8. Scheduled release checking/update

Start with check-only. In DSM open **Control Panel → Task Scheduler → Create →
Scheduled Task → User-defined script**. Use a deployment administrator/root account
with the required Docker access. Set a schedule in DSM; this package does not create it.

Script for staging pre-releases:

```sh
sh /volume1/docker/partflow/repo/deploy/synology/release-check.sh --channel prerelease
```

With only `v0.1.0-alpha.1` published, the default `stable` channel has no eligible
release. That is expected, not a reason to silently deploy `main`.

For unattended staging application, edit `deploy/synology/pf-config.json`:

```json
{
  "repository": "CDSemi/part-flow",
  "branch": "main",
  "project": "partflow-staging",
  "environment": "staging",
  "release_channel": "prerelease",
  "auto_update": true,
  "ci_workflow": "ci.yml",
  "health_timeout_seconds": 180,
  "minimum_free_mb": 2048
}
```

Then schedule the following **within an approved maintenance window**, for example a
nightly staging window, not while testers are entering data:

```sh
sh /volume1/docker/partflow/repo/deploy/synology/release-check.sh --apply
```

Both the flag and configuration opt-in are necessary. Automatic updates require a
published release, successful CI for its exact SHA, a clean managed Git checkout,
a descendant commit (no automatic downgrade or divergent branch), unchanged migration
files and schema head, and no changes to the reviewed deployment/configuration files
listed in `AUTO_REVIEW_PATHS` in the controller. The exact target images must build and
pass the health checks. A target requiring migration is deferred for a manual update.

The script serializes managed operations with an OS file lock. An interrupted operation
leaves a persistent journal blocking later automatic updates. It does not automatically
restore an old database after a health failure; that could hide newer writes. There is
no promise of zero downtime or complete application correctness from these gates.

| Exit code | Meaning |
| --- | --- |
| `0` | Completed, check-only result, no new SHA, or no eligible published release |
| `1` | Error, refused destructive operation, disabled automation, or incomplete operation |
| `2` | CLI/runtime prerequisite error |
| `20` | Update deferred: CI not ready, migration/configuration change, divergent source, etc. |

Enable DSM task result/failure notifications and inspect stdout/stderr. Deferred updates
use a nonzero code so they are not mistaken for an applied release. The public repository
does not require a GitHub token for ordinary reads. Optional `GITHUB_TOKEN` is read from
the process environment for API rate limits; do not put it in a URL, tracked file or log.
Git clone authentication for a future private repository is not configured by this token.

## 9. Scheduled backups

The new `deploy/synology/backup.sh` creates a revision checkpoint, not the old four-file dump layout:

```sh
sh /volume1/docker/partflow/repo/deploy/synology/backup.sh
```

Schedule it separately from update tasks. Standalone backup does not pause the app;
PostgreSQL supplies the logical dump's consistent snapshot. Restore verification creates
and removes a temporary database. Allow space for the dump, source archive, verification
DB, candidate images, and a retained pre-reset/pre-restore database.

Completed checkpoints are automatically published to the configured
`backup_read_group` with directory mode `0750` and file mode `0640`. They can be
browsed over SMB, for example:

```text
\\NAS\docker\partflow\backups\revisions\partflow-staging
```

If Windows still reports `Access denied`, run:

```sh
sudo sh ./pf.sh doctor
id YOUR_DSM_USER
```

and verify that the user belongs to the configured `backup_read_group` and that
the `docker` shared folder grants that group read access. Do not make recovery
artifacts world-writable with `0777` or `0666`.

Copy the entire checkpoint directory to an encrypted off-NAS destination. Configure
retention and alerts separately. This package never silently deletes old checkpoints,
retained images, `pf_keep_*` databases, or failed verification databases. They consume
space until an administrator reviews and removes them under a recovery plan.
`minimum_free_mb` is only a floor on the source/backup filesystem, not a capacity proof
for a Docker database volume that may live elsewhere.

## 10. Failure recovery

```sh
sudo sh ./pf.sh status
sudo sh ./pf.sh logs --tail=150 backend frontend db
```

Read the operation phase and checkpoint ID. If failure occurred before source/live DB
changes (`paused` or `backup-ready`), fix the error and use `resume`; it rechecks the
current schema/image relationship and requires `RESUME <database-name>`.

After an application-only update failure with no migration, code-only rollback remains
available if its schema gates pass. If a migration, reset, database restoration, or an
uncertain source/data change occurred, use the verified earlier checkpoint with
`rollback BACKUP_ID --restore-db` after reviewing its data-loss boundary. Incomplete
recovery may create an emergency data-preservation checkpoint that cannot be used as a
verified source rollback target.

Managed one-off jobs are labelled; on a caught failure the controller attempts to stop
its jobs and frontend/backend. Power loss or a hard kill cannot run cleanup: after a NAS
reboot inspect the journal, containers and database before exposing the app again. Do
not simply delete `pending.json` or start the frontend through the DSM UI to bypass it.
Manual Docker/DSM actions are outside the script's lock and guards.

Do not run `docker system prune -a`, delete volumes, or prune retained images while
these checkpoints are your rollback plan. Full physical-host disaster recovery and
automatic business reconciliation remain outside this staging helper.

## 11. Validation boundary and first NAS rehearsal

See `deploy/synology/TEST_REPORT.md`. The delivered controller was checked with real filesystem/archive
operations, real local Git clone/checkout, shell syntax checks and offline workflow
simulations. Docker, actual PostgreSQL 16 SQL execution and Synology were **not run**
in this environment. The full live clone from GitHub could not be exercised here because
the code sandbox could not resolve github.com; repository inspection used the GitHub
connector. The NAS must have its own working DNS and HTTPS access.

Before enabling `--apply`, use disposable staging data to rehearse: backup, one code-only
update/rollback, reset and restore, rejected confirmation, and a simulated service outage.
Verify counts/history in the UI and off-NAS backup access. Do not run the application's
integration test suite against data you intend to keep.

## 12. Sources

- [PartFlow deployment and release policy at the inspected commit](https://github.com/CDSemi/part-flow/blob/8d358eea0582b2e910df60569ad9865fd78f9d98/docs/DEPLOYMENT.md)
- [PartFlow CI workflow at the inspected commit](https://github.com/CDSemi/part-flow/blob/8d358eea0582b2e910df60569ad9865fd78f9d98/.github/workflows/ci.yml)
- [GitHub REST releases](https://docs.github.com/en/rest/releases/releases)
- [GitHub REST workflow runs](https://docs.github.com/en/rest/actions/workflow-runs)
- [PostgreSQL 16 ALTER DATABASE](https://www.postgresql.org/docs/16/sql-alterdatabase.html)
- [PostgreSQL 16 database rename implementation](https://github.com/postgres/postgres/blob/REL_16_STABLE/src/backend/commands/dbcommands.c)
