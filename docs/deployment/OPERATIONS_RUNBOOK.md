# PartFlow Operations Runbook

> **Status:** Canonical operational procedure template for Phase 16. The
> production Compose file exists (P16-S2): `PF` below is
> `docker compose -f compose.production.yaml --env-file .env.production`, run
> from the release checkout ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1). The
> release, migration, write-freeze and rollback commands (paths 1 and 2) are
> real (P16-S3: `deploy/production/release.sh`, `smoke.sh`, `migrate`,
> `revision`); the backup, restore-drill and rollback path 3 commands are real
> (P16-S5: `deploy/production/backup.sh`, `deploy/production/restore-test.sh`,
> `backup-manifest`, `backup-verify`, `backup-rotate`), though no schedule,
> off-host replication, drill or path 3 has been executed on a pilot host yet
> (P16-S7); the monitoring commands are real (P16-S6:
> `deploy/production/check.sh`, `deploy/production/scheduled-reconcile.sh` and
> `python -m app.cli status`), though their schedules, the failure notification
> and every pilot-host execution belong to P16-S7.
>
> **Language:** English is the source of truth. [Tiếng Việt](./OPERATIONS_RUNBOOK.vi.md).

## 1. Required deployment record

Record for every environment and release:

| Field | Value |
| --- | --- |
| Environment and URL |  |
| Host/model/provider |  |
| Release Git commit/tag and image digests |  |
| Alembic revision before/after |  |
| Deployment operator and approver |  |
| Start/end time (UTC) |  |
| Pre-release backup path, checksum, and verification |  |
| Migration output (including the grants report) and database-role provisioning reports |  |
| Smoke/reconciliation results |  |
| Rollback deadline and observation owner |  |
| Known limitations |  |

`deploy/production/release.sh` writes this record as `record.json` (with the
output of every step) in `<records-dir>/<UTC>-<tag>/`, and the fields map to the
rows above: `environment` and `url` (row 1), `host` (row 2), `release` (tag,
commit, previous tag and the local image IDs of `backend` and `web`; row 3),
`alembic` (`before`, `after`, `expected`; row 4), `operator`, `approver`
(row 5), `started_at`, `finished_at` (row 6), `backup` (row 7: `kind`, `reference`, `name`, the absolute `path`,
`verified`, `verification`, `freshness`, `taken_by` (`release.sh`, `operator` or
`null`), `manifest_sha256` and the manifest facts of §3), `migration` (the `migrate.json` and `migrate.log`
files; row 8; `migrate.json` carries the `grants` result, and the `provision-roles` and `apply-grants` JSON reports of a manual run are kept beside it), `reconcile` and `smoke` (rows 9), `rollback_deadline` and
`observation_owner` (row 10), `known_limitations` (row 11); `freeze_completed_at` (the instant `backend` was confirmed stopped; `null` when
no freeze happened), `outcome`, `writes_reopened_at` and `refrozen` state how the
run ended. A manual operation (for example a rollback) appends to the same
directory with the same fields. The restore-drill evidence (`evidence.json`, §4)
and the path 3 `rollback.json` and new-instance `restore.json` (§6) are written
under the same records directory.

## 2. Health and diagnosis

Minimum checks (development and staging stack):

```bash
docker compose ps
docker compose logs --since=15m backend frontend db
curl --fail --silent --show-error https://<partflow-host>/api/health
```

Production stack (`PF` as above; health through the hostname):

```bash
$PF ps
$PF logs --since=15m backend web db
curl --fail --silent --show-error https://<partflow-host>/api/health
curl --fail --silent --show-error https://<partflow-host>/api/health/live
$PF run --rm --no-deps -T backend python -m app.cli revision
```

Production monitoring commands (P16-S6; run from the running release's checkout
as the account that owns `PARTFLOW_BACKUP_DIR`; the schedules are installed on
the pilot host in P16-S7):

```bash
# manual diagnosis: the same checks as the scheduled run, without touching its state
deploy/production/check.sh --url https://<partflow-host> --no-state
# database size, connections, lock waits, Movement rows and size, schema readiness, backup age
$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status
docker stats --no-stream
```

`check.sh` prints one line per check, `PASS|FAIL|SKIP <id> <reason>`, in this
order: `https` (`/api/health` answers 200 with `schema` current and the release
of `.env.production`), `certificate` (not expiring within 21 days), `containers`
(exactly one `running` `db`, `backend` and `web`, healthy), `restarts` (no
restart-count increase since the previous full run), `errors` (no `ERROR` or
`CRITICAL` backend record since the previous full run, with up to three request
ids), `disk_data` (the database volume), `disk_backup` (the backup directory),
`disk_docker` (the Docker root: images and container logs), `disk_archive` (the
archive directory; `SKIP` until one is configured, P16-S8), then `database`,
`schema`, `backup_age` and `archival_proposal` from one `status` run (the last
is `SKIP` until P16-S10). A disk is a `FAIL` below 15 % free and `backup_age` a
`FAIL` when the newest published backup of any kind is older than 26 hours. Exit
codes: 0 nothing to notify, 1 at least one `FAIL` to notify, 2 the check could not
run (also notifies). An unchanged failing set is notified again only after 6
hours (`--renotify-hours`; the output then says `already reported`). State lives
in `~/partflow-monitoring` (`--state-dir`, mode 0700): `restarts` (restart
baseline), `errors-since` (log cursor), `alert-state` (re-notification),
`last-check.txt` (every run), `status.json`, `status.err` and `growth.tsv` (one
dated line per day: database bytes, Movement rows and Movement bytes). **Always
add `--no-state` to a manual run:** without it the manual run advances the error
cursor and the restart baseline, so the next scheduled run would miss the errors
and restarts the manual run already saw.

`status` prints one JSON document (`result` `ok`, `attention` or `error`) with
the exit code 0, 1 (a finding such as `backup_stale` or `schema_not_ready`) or 2
(`configuration_invalid`, `database_unavailable`, `lock_timeout`,
`statement_timeout`, `database_error`, `internal_error`). `database.locks`
holds `waiting` and `longest_wait_seconds`, the lock-wait figure of the checklist
below; it is reported, never a threshold. It runs one read-only transaction as
the application role with a 5 s lock timeout; keep `--statement-timeout` (default
20 s) below 30 s so a release's `migrate` is never aborted by it.

Logs. In the production stack the backend writes one JSON object per line
(`ts`, `level`, `logger`, `message`, `request_id`, then the record's own fields).
Every response, refusals and failures included, carries the **HTTP request id**
in the `X-Request-ID` header (a client's own value is kept when it is 1-64
characters of `A-Za-z0-9._-`, otherwise the backend generates one), and every
backend record of that request carries it as `request_id`. To find a request:

```bash
$PF logs --no-log-prefix backend | grep '"request_id":"<id>"'
```

The HTTP request id changes on every resubmit. A production command is therefore
always correlated and retried by its `device_event_id` (the GUI's "request
identity"), never by `request_id`. To follow a command or a Station, filter the
`app.access` records by the fields they carry:

```bash
$PF logs --no-log-prefix backend | grep '"device_event_id":"<id>"'
$PF logs --no-log-prefix backend | grep '"part_number":"<PN>"'
$PF logs --no-log-prefix backend | grep '"quantity_flow_id":<n>'
$PF logs --no-log-prefix backend | grep '"area_id":<n>'
$PF logs --no-log-prefix backend | grep '"station_id":"<id>"'
```

An `app.access` record holds `event`, `method`, `route` (the route template;
`path` only when no route matched), `status`, `duration_ms`, `client`, `outcome`
(`ok`, `created`, `replayed`, `refused` or `error`), `slow`, `refusal` (`type`,
`code`, `message`) and `context`: the PN, QuantityFlow, quantity, Area,
Operation, Machine, Work Order, `device_event_id`, Scan Station, `user_id` and
`worker_id` that the request named. `worker_id` appears only on records of a
command that reached identity resolution (a created command or an identity
refusal): the Worker of a replayed command is in its Movement. Never logged:
request or response bodies, query strings, cookies, device tokens, CSRF tokens,
badges, passwords, scanned values and PostgreSQL `DETAIL` lines (the database
runs with `log_error_verbosity=terse`). The first-run setup token is the one
exception: it is printed once, until setup is complete. Routine reads below 1
second and every health poll are not written at `INFO` (`"slow":true` marks a
read of 1 second or more); designed refusals (`not_ready`, `release_mismatch`,
`password_check_busy`, a rejected request) are `INFO`, and only a real failure is
`ERROR`. Measured backend volume in the rehearsal: about 1.1 MB per 1,000
commands, so the 10 MB x 5 json-file rotation of the stack holds about 9 days of
log at 5,000 commands per day; the rehearsal was synthetic, so P16-S7 re-measures
it on the pilot host. `web` writes one JSON edge record per request with the same
`request_id` (and the upstream status), so a `502` or `504` from `web` is found by
its `request_id` in the `web` log like any other request; measured `web` volume in
the same rehearsal: about 0.6 MB per 1,000 commands (about 18 days of rotation at
5,000 commands per day).

`/api/health` is readiness: it reports `release`, `commit`, `schema`
(`current`, `accepted`, `mismatch` or `unknown`), `expected_revision`,
`database_revision` and `accepted_revision`, and answers 503 on a schema
mismatch or an unreachable database. `/api/health/live` is liveness only (the
container health checks use it): it stays healthy while readiness is 503 during
a schema mismatch. `revision` prints the same facts from a one-off container
and works while `backend` is stopped.

`web` writes the edge record of every request (JSON: client address, method, path
without query string, status, bytes, duration, upstream status, user agent,
`request_id`) and the backend the application record of every write, refusal,
failure and slow read; neither contains query strings, cookies or PartFlow
headers. A 502 or 504 JSON
answer comes from `web` (`server_unavailable`) and means the outcome of a write
is unknown: resolve it with the original `device_event_id` (below).

In the production stack the backend connects as `partflow_app`. A `permission denied` (SQLSTATE 42501) in the backend log means a code path tried to change protected history or lacks a grant: an incident (§8), never fixed by granting more. Run `reconcile --check h` (§7) before anything else.

Then check:

- host CPU, RAM, disk, I/O, time, and recent reboot;
- container restart counts and health status;
- reverse-proxy status and certificate expiry;
- PostgreSQL connections, locks, storage growth, and backup age;
- browser connectivity state and whether writes are correctly blocked;
- errors correlated by PN, QuantityFlow, Area, Operation, Machine, Worker, Scan
  Station, and `device_event_id` where relevant.

Do not retry a timed-out production command with a new `device_event_id` until
the original result is resolved. Query/retry with the original idempotency key
so an uncertain client response cannot duplicate a write.

## 3. Logical database backup

`deploy/production/backup.sh` (P16-S5) takes the backup. Run it from the
repository root of a release checkout (the running release's for scheduled and
manual backups; `release.sh` calls it from the candidate's) with the production
stack's `db` running. Its dump is streamed from **inside `db`**
(`pg_dump --format=custom --no-owner --no-privileges --lock-wait-timeout=60s`,
one snapshot, client and server are the same binary), listed with `pg_restore
--list`, and published with a manifest and checksums by the `backup-tools`
one-shot service (no network, the backup directory only). The repository
provides the artifact, its verification and the retention; encryption and
off-host replication belong to the platform tool (below).

### Artifact and name

One backup is one directory `<backup-dir>/<NAME>/`, where `<backup-dir>` is the
`PARTFLOW_BACKUP_DIR` of `.env.production` and

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-daily
                                         and 20261008T140000Z-pre-release-v1.0.0-rc.2
```

`kind` is `daily`, `manual` or `pre-release`; `label` (the target release tag) is
required for `pre-release` and forbidden otherwise; the stamp is the UTC start of
`backup.sh`. The directory holds exactly `partflow.dump`, `partflow.dump.list`
(`pg_restore --list`), `manifest.json` and `SHA256SUMS`. It is assembled under
`<backup-dir>/.partial/` and published by one atomic rename, so a backup either
exists completely or not at all; a crash leaves only `.partial/` entries. Entries
that are not part of the grammar (for example a DSM `@eaDir`) are ignored and
reported, never fatal.

### Taking a backup

```bash
# scheduled daily backup (14 and 8 are placeholders for the owner's retention)
deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
# manual backup (a reason is required)
deploy/production/backup.sh --kind manual --operator "<name>" --reason "<why>"
```

A `pre-release` backup is taken by `release.sh` (§5); the manual form is
`backup.sh --kind pre-release --label <target tag> --tools-release <target tag>
--operator "<name>"`. On success stdout is exactly one line,
`BACKUP <NAME> <absolute path>`; progress and the tool reports go to stderr.
`deploy/production/backup.sh --help` prints every option:

| Option | Meaning |
| --- | --- |
| `--kind daily\|manual\|pre-release`, `--operator NAME` | required |
| `--reason TEXT` | required for `manual`; defaults to `scheduled daily backup` (daily) and `pre-release backup for TAG` (pre-release) |
| `--label TAG` | the target release tag; required for `pre-release`, refused otherwise |
| `--tools-release TAG` | the `backup-tools` image that writes and verifies the manifest (default: `PARTFLOW_RELEASE` of the env file; use the candidate tag when the running release predates P16-S5) |
| `--keep-daily N --keep-weekly N` or `--no-rotate` | `daily` only; the retention of the owner (below). There is no default: the scheduled command states the values |
| `--reserve-mib N` | free space kept beyond twice the newest dump (default 1024) |
| `--lock-held-by-release DIR` | `release.sh` only (it holds the backup lock) |
| `--rehearsal --project NAME` | a throwaway Compose project (never `partflow-production`) |

Text values are 1-500 characters without control characters, `"` or `\`. What
each step does, in order (`backup: <step> ok (<ms> ms)` on stderr):

1. `preflight`: tools, the env file (`PARTFLOW_RELEASE`, `PARTFLOW_BACKUP_DIR`),
   `config`, `db` running, the `backup-tools` image, and the backup lock (below);
2. `identify`: the running release, commit, image IDs and the expected Alembic
   revision (best effort, never fatal);
3. `space`: at least twice the newest dump (or the database size when there is
   none) plus the reserve must be free in the backup directory, else it is refused
   and nothing is written;
4. `dump`, `list` and `revision`: the three exec calls inside `db` with `TZ=UTC`
   (the archive stores local-time fields);
5. `manifest`: `backup-manifest` publishes the directory; `verify`:
   `backup-verify` checks it; `rotate`: `backup-rotate` for a `daily` backup;
   `done`.

| Exit | Meaning | Do |
| --- | --- | --- |
| 0 | completed | - |
| 1 | refused before writing (`backup_running`, `backup_lock_stale`, `insufficient_space`, `name_exists`) | read the printed reason; nothing was written |
| 2 | could not run (usage, tools, environment, `db` not running, the backup lock cannot be created) | fix and rerun |
| 3 | failed: no backup was published, or the published backup failed verification | do not use a backup the message names; remove it after review |
| 4 | the backup is complete and verified, but the rotation failed or found a daily backup that fails verification | review the `backup-rotate` report on stderr; see Retention |

Every non-zero exit is reported by the host scheduler's failure notification.

### Manifest and verification

`manifest.json` (`manifest_version` 1) maps to the deployment record fields of §1
and to what a restore needs:

| Record field | Manifest key |
| --- | --- |
| UTC timestamp | `dump_started_at`, `completed_at` |
| environment, host | `environment`, `host` |
| Git commit/image digests | `release.commit`, `release.tag`, `images.backend`, `images.web`, `images.db` |
| Alembic current revision | `alembic_revision` (and `alembic_rows`); `release.expected_revision` is what the running release expected |
| PostgreSQL major version | `database.server_major` (and `server_version`, `pg_dump_version`, `database.name`) |
| dump/list checksums | `files[].sha256` and `SHA256SUMS` |
| operator and backup reason | `operator`, `reason` |

It also records the `kind`, the `label`, the `tool` image that wrote it, and the
`dump` facts (`options`, `toc_entries`, `table_data_entries`, `tables_checked`,
`extra_tables`). `tables_checked: true` means every table of the release's table
classification has a data entry; a dump of another revision is published with
`tables_checked: false`. An `alembic_version` that does not hold exactly one valid
revision, or tables the release does not know, are published with a warning
(a faithful backup of a drifted database is worth more than none); `migrate`
refuses such a backup (§5) and the restore drill fails at its revision step.

Verify any backup (read-only; the same checks run in `migrate` and in the
drill), and independently of any image after any copy:

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"
(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)
```

(prefix the first command with `PARTFLOW_RELEASE=<tag>` to use another release's
`backup-tools` image). `backup-verify` prints one JSON document with the checks
`directory`, `files`, `sha256sums`, `manifest`, `dump_header`, `list` and
`expect_database` (`--expect-database NAME`), exit 0 `verified`, 1 `invalid`
(`backup_invalid`: do not use the backup) or 2 `failed`. Without `--user` the
container cannot read the 0700 backup directory.

### Backup directory

- an existing absolute directory, outside the repository checkout, the secrets
  directory and any archive directory; **production only** (never staging's or a
  drill's); mode 0700, owned by the account that runs `backup.sh` and
  `release.sh`; files 0600; created before the first `$PF` command, because every
  Compose command of the stack needs `PARTFLOW_BACKUP_DIR` (`DEPLOYMENT.md`
  §3.1);
- a dump holds every table, including credential hashes and session and device
  token digests: treat the directory and every copy as secret;
- free space: `2 x` the newest dump plus the reserve (default 1 GiB).

Dumps carry no grants by design (`--no-privileges`): the database roles and their privileges are re-derived after a restore (§4).

### Retention, off-host copy and alerts

`backup.sh --kind daily` applies `backup-rotate` (it can be run alone,
`--dry-run` reports without deleting):

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-daily 14 --keep-weekly 8 --dry-run
```

It keeps the newest `--keep-daily` verified daily backups plus the newest daily
backup of each of the newest `--keep-weekly` ISO weeks (UTC; the weeks overlap the
daily window, like restic `forget --keep-daily --keep-weekly`). Every candidate is
verified first; one that fails is not counted and **never deleted** (exit 4: the
operator reviews it, §9). **Pre-release and manual backups are never rotated**
(the operator removes them after the observation window, §9), and **an archive
directory is never rotated or pruned by any tool**, platform tools included.
Leftovers in `.partial/` older than 24 hours are removed. The values 14 and 8 are
placeholders: the owner sets the real ones in the scheduled command; they are
required options, never code defaults.

Encrypt and copy the backup directory off-host with the platform tool, never by
hand: Hyper Backup with client-side encryption to an off-NAS target on Synology
(`SYNOLOGY_NAS.md` §6), restic to object storage on a VPS (`VPS.md` §8). Alerts:
a failed scheduled run (any non-zero exit, a daily that fails verification
included) is the scheduler's failure notification, and a failed replication is the
platform tool's own notification; both are reviewed daily (§9). The "backup too
old" alert is `check.sh` `backup_age`: the newest published backup of any kind
older than 26 hours (§2, §9; P16-S6). It is installed on the pilot host in
P16-S7. The recovery point objective is the daily schedule
(24 hours) until the owner approves the RPO and RTO (P16-S7).

### Backup lock

Backups are serialized by the directory `<backup-dir>/.backup.lock` (its `owner`
file holds `host`, `pid`, `started_at`, `by=backup.sh|release.sh` and `name` or
`release`). A second run is refused, never queued: `backup_running` means a
backup (or a release holding the lock) is in progress, so wait. `release.sh`
holds the lock from the write freeze (or the backup step) through the `backend`
switch, so schedule daily backups outside maintenance windows: a daily that is
running stops a release before the freeze with nothing changed, and a daily that
starts during a release is refused. A lock that cannot be created at all (the
backup directory is full, out of inodes or read-only) is reported with the
`mkdir` error as could-not-run (exit 2), never as `backup_running`; `release.sh`
then stops with `could_not_run` before the freeze, nothing changed.

`backup_lock_stale` means the lock was left by a run that no longer exists (a
kill, an out-of-memory kill or a power loss); it is never broken automatically.
Recover by hand:

```bash
cat "<backup-dir>/.backup.lock/owner"   # host, pid, started_at, by and name|release
ps -p <pid>                             # on that host: must report no such process
$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"'   # must print 0
rm -r "<backup-dir>/.backup.lock" "<backup-dir>/.partial/<name>"   # the name from the owner file, when it has one
```

Never remove a lock whose owner is `release.sh` while that release runs. While
`release.sh` holds the backup lock (or its release lock exists) `check.sh` skips
its `status` checks (§5 Observe).

## 4. Restore test — never overwrite first

Restore into an isolated database or isolated stack, never directly over the
only production database:

1. verify dump and manifest checksums;
2. provision the same PostgreSQL major version or a documented compatible
   target;
3. create an empty restore-test database;
4. restore with `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`
   (one transaction: a failed restore leaves the target empty);
5. start the matching application release against that database;
6. verify Alembic revision;
7. run health, representative read models, quantity/movement/allocation
   reconciliation, and designated smoke tests;
8. record restore duration and result;
9. destroy the isolated restore copy only after evidence is retained.

In a restore into a new cluster, provision the database roles first (`provision-roles`, with throwaway passwords in a restore drill), and run `apply-grants` against the restored database before starting the application (step 5): a dump contains no grants, so a restored database has none until `apply-grants` re-derives them, and the application role cannot work without them. Reconcile check (h) must pass before a restored database serves production: path 3 and the new-instance restore (§6) run `reconcile` before `backend` starts. `restore-test.sh` restores in its own project (below) and runs the full `reconcile` once the application is ready; a check (h) that is not `pass` fails the drill.

With the production stack the whole procedure is `deploy/production/restore-test.sh`
(P16-S5), run from the repository root of a release checkout:

```bash
deploy/production/restore-test.sh --backup <NAME> --operator "<name>"
# a candidate PostgreSQL image (glibc or image change), a baseline of known findings:
deploy/production/restore-test.sh --backup <NAME> --operator "<name>" \
    --db-image postgres:16.14-bookworm --baseline-report <records>/<release>/pre-reconcile.json
```

`deploy/production/restore-test.sh --help` prints every option: `--release TAG`
(the release to run on the restored database; default the release in the
manifest, and a backup taken between a migration and the environment rewrite needs
it named), `--tools-release TAG`, `--db-image IMAGE`, `--baseline-report FILE`,
`--project`, `--http-port`, `--edge-subnet`, `--records-dir`, `--space-factor` and
`--keep`.

Isolation, enforced by the script: its own Compose project
`partflow-restore-<suffix>` (never `partflow-production`), with its own volume and
networks, its own loopback port and edge subnet (neither the production ones), the
fixed database name `partflow_restore_test`, **generated throwaway database-role
passwords** (secret files removed at teardown), one backend worker, and the backup
directory mounted **read-only**; it never runs `migrate`, never writes to the
backup directory and refuses a project that already has containers or volumes. It
needs `5 x` the dump plus 1 GiB free on the Docker root (`--space-factor`).

The script performs the nine steps above and records each one in the evidence:
`verify` (step 1: `backup-verify`), `db_start` (steps 2 and 3: an empty database
on the PostgreSQL image that the checkout's `compose.production.yaml` pins, or
`--db-image`; the evidence compares its major with the manifest's), `restore` (step 4, one
transaction), `roles` and `grants` (`provision-roles`, then `apply-grants` before
the application starts), `revision` (step 6: state `current` and the manifest's
revision), `app_start` (step 5 and the health of step 7: `backend` and `web` up,
readiness `current`), `reconcile` (the full `reconcile`: its checks (a)-(f) replay
Movement history against every stored projection and allocation, which is the
quantity, movement and allocation evidence of step 7), `smoke`
(`deploy/production/smoke.sh`, the designated smoke), `evidence` (step 8) and
`teardown` (step 9: `down -v` of the drill project only, after the evidence file
exists and only when this run created the project; `--keep` keeps it and prints
the command).

Pass rule: `passed` needs reconcile exit 0 **and** check (h) `pass` **and** check
(j) `pass` (grants are freshly applied on the drill, so a (h) finding or
`not_applicable` is a real defect; with `--db-image` check (j) is the glibc and
PostgreSQL-image half of the platform-upgrade identity check, §7).
`passed_with_preexisting_findings` needs `--baseline-report` and every finding,
(j)'s included, to exist in that report (`reconcile_regression.py`), with (h)
`pass`. Anything else is `failed`. The evidence
`<records-dir>/<UTC>-restore-test-<NAME>/evidence.json` records the outcome, the
failed step, the backup and drill facts, the drill server's version, collation and
collation versions, the backup's and the drill server's PostgreSQL majors with
`server_major_match` (`false` is also printed as a warning: not a same-major
drill; `null` when either is unknown), the per-step timings (`restore_to_ready` and `total` are the
RTO measurement inputs) and the reconcile statuses.

| Exit | Meaning |
| --- | --- |
| 0 | `passed` or `passed_with_preexisting_findings` |
| 1 | `failed`, or not enough free space |
| 2 | could not run, or interrupted (nothing created is left running unless `--keep`) |
| 3 | teardown failed: the evidence stands, the message prints `docker compose -p <project> down -v` |

**Recorded limitation:** an authenticated read-back of individual read-model
screens is not automated (it needs a real account of the restored data); the
evidence states `read_model_readback: not_automated`. The owner may check it by
hand: run the drill with `--keep`, sign in to the printed loopback port, and tear
the project down with the printed command afterwards.

Use explicit restore-test names. Never substitute the production database name
in a rehearsal command.

## 5. Release and migration

### Before maintenance

- approve exact release revision and scope;
- confirm CI/quality results for that revision;
- review every migration and its downgrade/recovery behavior;
- estimate lock/time/disk impact using staging data;
- verify off-site backup health (the platform tool's last notification) and that
  the newest backup is recent (§3); `release.sh` takes the verified pre-release
  backup **inside the write freeze** when a migration is pending (no window
  between the backup and the migration), or before the switch otherwise;
- verify the previous release remains available;
- run reconciliation (§7) on the current release, keep the report, and open
  incidents for any findings; in the production stack run it, and any recovery
  CLI, with the **current** tag, before the candidate tag is written to
  `.env.production` (the candidate lives only in the shell until the switch);
- decide whether writes must be stopped (a pending migration always needs the
  write freeze below);
- announce the window and rollback decision deadline, and ask stations to
  finish their open dialogs;
- schedule daily backups outside maintenance windows: a running backup stops a
  release before the freeze with nothing changed (`backup_running`, §3), and a
  daily that starts during a release is refused.

### Write freeze

The write freeze is **stopping `backend`** (`$PF stop backend`); there is no
other application mode. `uvicorn` finishes in-flight requests, an import
included, and the stop waits up to 200 s; with more than one worker the
supervisor keeps the socket open until the last worker exits, so requests sent
meanwhile hang and end as a 504 or 502 (DEPLOYMENT §3.1 Process model): start
the freeze when no import is running. While `backend` is stopped `web` still
serves the shell and answers `/api/*` with its JSON 504 and then 502, every
client turns to the OFFLINE banner within about a second and every write control
is blocked. Open dialogs keep their drafts; a write that raced the stop is an
unknown outcome that keeps its `device_event_id` and is retried with it after a
reopen on the **same** release. After a reopen on a **new** release the old page
cannot send it (it is refused with 409); the operator reloads and checks the
Area or Work Order before repeating the action. A cut import leaves only whole
committed Work Orders. `reconcile`, `revision`, `migrate` and the backup run from
one-off containers against `db` while frozen. **Reopen** with
`$PF up -d backend` on the same release, only after the checks of the step
below; at a release switch the reopen is the `web` switch.

### Execute

Run `deploy/production/release.sh` from the repository root of the release
checkout, with the release tag checked out:

```bash
deploy/production/release.sh --release <new-tag> --operator "<name>" --approver "<name>"
```

(`deploy/production/release.sh --help` prints every option.) With neither backup
option `release.sh` takes the verified pre-release backup itself.
`--pre-release-backup <NAME>` names an existing `backup.sh` backup and is only for
a release **without** a pending migration (with one, `release.sh` stops with
nothing changed, because the backup must be taken inside the freeze);
`--no-backup-reason "<text>"` is only for a first installation or a rehearsal
(recorded). It runs, recording each step: preflight (tools, the environment file,
the tag form, clean build inputs); the current revision and the pre-release
reconcile with the running release; the candidate build (an existing tag is
never rebuilt, and is reused only when both images were built from this commit
as this release); the candidate's check (j) and revision (which must report this
release and commit); the write freeze when a
migration is pending; the verified pre-release backup (step 6a,
`pre_release_backup`: `backup.sh --kind pre-release`, after the freeze when a
migration is pending, holding the backup lock from the freeze through the
`backend` switch); `migrate` (which verifies the backup first and also applies the grants); the post-release reconcile; the `backend`
switch while `web` still serves the previous bundle (writes stay refused, every
loaded page sends the previous release and gets 409), the health wait for the
new release and a `current` schema; the `web` switch, which reopens writes; and
`smoke.sh`. A failed check after the switch stops `backend` again. Its preflight also refuses, with nothing changed, while `partflow_app_password`, `partflow_maintenance_password` or `postgres_password` is missing, empty or not a regular file.

`migrate --pre-release-backup NAME` verifies the backup with the `backup-verify` rules before it connects, and accepts it only when it is fresh and belongs to this database: the dump must have **started at or after** the write-freeze time (`--backup-not-before`, which `release.sh` passes: the freeze time when a migration is pending, otherwise its own start or the newest completed record), so it holds every write `backend` committed; without that option a 60-minute age limit applies, but only when no migration is pending (with one, `migrate` refuses `backup_freshness_unproven`). The backup's revision and database name must equal the database's. A refusal (`backup_not_found`, `backup_invalid`, `backup_stale`, `backup_revision_mismatch`, `backup_freshness_unproven`, `backup_database_mismatch`; exit 1) changes nothing and, while frozen, reopens writes on the current release like any refusal; an unreadable backup directory is `backup_unreadable` (exit 2: the container needs `--user "$(id -u):$(id -g)"`, which `release.sh` passes). Free text is refused: `--pre-release-backup` names a backup directory.

Every `migrate` applies the grants in the same transaction as the upgrade. A `refused` result with a database-role code (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`, `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) rolls the whole run back and stops the release before anything changes (exit 1, as any other refusal); read the printed message, fix the cause (`$PF --profile ops run --rm -T db-roles` for a role code, `DEPLOYMENT.md` §3.1) and rerun. On a first installation the order is: secret files, build, `db`, `db-roles`, `migrate`, first-run setup (`DEPLOYMENT.md` §3.1).

| Exit | Meaning | Do |
| --- | --- | --- |
| 0 | completed | observe (below) |
| 1 | stopped with nothing changed, or writes reopened on the current release | read the printed reason and `record.json`; fix; rerun |
| 2 | could not run (usage, tools, environment) | nothing changed; fix and rerun |
| 3 | `backend` left stopped | follow §6; read `regression.txt` or compare `pre-reconcile.json` and `post-reconcile.json` |
| 4 | the new release may be running and writable after a failed check, and the re-freeze failed | run `$PF stop backend` yourself, then follow §6 |
| 130, 143 | interrupted by Ctrl-C or TERM (record `outcome: interrupted`, the last step names where); no service was started or stopped by the interruption | interrupted during `migrate`: the outcome is unknown (record `migration.result: outcome_unknown`, `alembic.after: null`), so run `$PF run --rm --no-deps -T backend python -m app.cli revision` before anything else; then `$PF ps` and §6 (after `switch_backend`, `.env.production` already names the new tag; `env-before.txt` in the record directory is the previous file) |

`--accept-pre-release-findings` continues when the pre-release reconcile has
findings and blocks only on findings absent from it
(`deploy/production/reconcile_regression.py` compares the two reports).
`--skip-pre-reconcile REASON` is for the rollback path 2 state in which no
image has the database revision as its head: the reason is recorded and every
post-release finding blocks.

The manual equivalent, in the same order, with the current tag in
`.env.production`:

1. Record the current application and Alembic revisions
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) and run the
   pre-release reconcile with the current tag (§7).
2. Build: `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`
   (never an existing tag), then rehearse the candidate image against the
   unchanged database:
   `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`
   (a failing check stops the release; nothing has changed).
3. Freeze when a migration is pending: `$PF stop backend`, confirm
   `$PF ps --status running -q backend` prints nothing, then record the instant,
   `T=$(date -u +%Y-%m-%dT%H:%M:%SZ)` (without a pending migration, record `T`
   before step 3a instead).
   3a. Take the backup from the candidate checkout:
   `deploy/production/backup.sh --kind pre-release --label <new> --tools-release <new> --operator "<name>"`;
   note the `BACKUP` line's NAME.
4. `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate --pre-release-backup NAME --backup-not-before "$T"`
   (`--no-backup-reason TEXT` instead of the backup only for a first installation
   or a rehearsal). Without `--backup-not-before`, `migrate` refuses a pending
   migration (`backup_freshness_unproven`), and without `--user` it cannot read the
   0700 backup directory (`backup_unreadable`).
   Capture its JSON output (the `grants` field reports the applied grants, the
   `backup` field the verification) and the new revision.
5. Run the post-release reconcile with the new tag (§7) and compare it with the
   pre-release report: only findings absent from it block step 7; pre-existing
   findings stay open incidents under the owner's decision.
6. Write `PARTFLOW_RELEASE=<new>` into `.env.production` (and clear
   `PARTFLOW_ACCEPT_SCHEMA_REVISION`), then `$PF up -d --no-deps backend`;
   check health: `release` is the new tag and `schema` is `current`. On a
   database with no Administrator, complete first-run setup (the setup token is
   in the backend log) before opening access; start the backend with one worker
   for it (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, then
   `$PF up -d backend`). Then enroll each Scan Station device (Administration
   → Scan Stations → `Devices…`).
7. `$PF up -d --no-deps web`: this reopens writes. Run
   `deploy/production/smoke.sh --release <new>` and the designated
   authorization, scan-focus/connectivity and write/read-back checks.
8. Reopen writes only when every required check passes; otherwise
   `$PF stop backend` again and follow §6. On exit 3 of `release.sh`, read
   `regression.txt` (or compare both reports) and then either reopen as in step
   6 and 7 or follow §6.

### Observe

Monitor errors, latency, locks, restarts, disk, and operator feedback through the
defined observation window: the `check.sh` lines (`errors`, `restarts`,
`disk_*`), slow reads (`"slow":true`) and `status`, including its lock waits
(§2). A check during the write freeze reports `backend` stopped (one
notification) and skips the `status` checks while the release lock exists. Retain the previous release and the pre-release backup (never rotated). Pages that
were open during the switch show the update notice and reload (GUI_DESIGN §3
rule 13); an unattended Scan Station or Production Board reloads itself once no
dialog is open.

## 6. Rollback decision tree

1. **No schema migration occurred:** redeploy the previous immutable application
   release: set the previous tag in `.env.production`, `$PF up -d backend web`,
   then `deploy/production/smoke.sh --release <previous>`. It needs the previous
   release's images on the host: keep them through the rollback window and never
   run `docker image prune -a`; `release.sh` refuses to start when they are
   missing.
2. **Schema migrated and is backward-compatible:** deploy the previous release
   only if compatibility was explicitly verified and recorded. Read
   `database_revision` from `$PF run --rm --no-deps -T backend python -m app.cli revision`
   (run with the new release), set `PARTFLOW_RELEASE=<previous>` and
   `PARTFLOW_ACCEPT_SCHEMA_REVISION=<database_revision>` in `.env.production`,
   `$PF up -d backend web`, then
   `deploy/production/smoke.sh --release <previous> --allow-accepted-schema`.
   The override names exactly one revision that the previous release does not
   know (a revision it knows is ignored, `revision` shows `override_ignored`,
   and readiness stays `mismatch`), it never matches any other revision, and the
   next forward release clears it. Before the override is set the previous
   release refuses every change; station reads still record the device's
   last-seen time (device bookkeeping, no production data). Releasing forward
   from this state uses the candidate image for the pre-release reconcile
   (`release.sh` does) or `--skip-pre-reconcile REASON`.
3. **Schema migrated and is not backward-compatible, or compatibility is
   unknown:** stop writes; restore the pre-migration database into a new,
   empty database in the production PostgreSQL instance (`createdb
   --template=template0`, restored in one transaction; the migrated database is
   preserved untouched) and deploy the matching previous application release,
   following *Path 3 procedure* below. A restore of the pre-release backup never
   discards writes made after it without the path 4 escalation.
4. **New production writes occurred after migration:** do not blindly restore
   over them. Escalate; preserve both the current database and pre-release
   backup, determine a forward fix or audited data-recovery plan, and keep the
   application write-blocked. The pre-release backup and the migrated database
   are both preserved.

Recovery CLIs run with the release that matches the database.

### Path 3 procedure

*Wording note:* before P16-S5 path 3 read "restore the pre-migration database
into a clean instance". P16-S5 implements it as a new database in the same
instance (it never overwrites, needs no volume change, and the database roles
are cluster-global); this amendment of the sentence awaits the owner's
acceptance (`IMPLEMENTATION_ROADMAP.md`, slice P16-S5). If the owner prefers a
new volume, step B changes and nothing else.

Path 3 applies only when the release record shows `writes_reopened_at: null`
(the release stopped frozen) or the owner records that no production write
happened after the migration; otherwise it is path 4. Restoring into production
needs the **owner's approval, recorded in `rollback.json` before `backend`
starts** on the restored database; without it the procedure stops after
`reconcile` with the application write-blocked.

The `.env.production` keys are read by Compose and are not exported to the shell,
so every value below is set explicitly. Each block is a script: save it in the
release record directory and run it with `sh -eu <file>`, never pasted line by
line. A guard that does not hold prints why and stops the script before the next
command, and so does any failing command (`-e`). Step A runs in the **candidate**
release's checkout (its `release.sh` took the backup, so it has the P16-S5
`backup-tools` even when the previous release predates P16-S5):

```sh
REC=<release record directory>; CAND=<candidate tag>; PREV=<previous tag>
# Path 3 only when writes were never reopened; drop this guard only for the owner's recorded exception.
python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["writes_reopened_at"] is not None)' "$REC/record.json" \
    || { echo "writes were reopened: path 4, not path 3"; exit 1; }
NAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup"]["name"])' "$REC/record.json")
BPATH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup"]["path"])' "$REC/record.json")
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF stop backend                                                  # keep or enter the write freeze
RUNNING=$($PF ps --status running -q backend)
[ -z "$RUNNING" ] || { echo "backend is still running: the write freeze is not in place"; exit 1; }
(cd "$BPATH" && sha256sum -c SHA256SUMS)                          # host check, independent of any image
PARTFLOW_RELEASE="$CAND" $PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" backup-tools backup-verify "$NAME"
echo "BPATH=$BPATH"
```

Step B runs in the **previous** release's checkout, with its images present (no
P16-S5 service is used from here on), as three scripts. B1 checks the space and
restores into a new database:

```sh
BPATH=<backup path printed by step A>
ENV=.env.production
PF="docker compose -f compose.production.yaml --env-file $ENV"
OLDDB=$(sed -n 's/^POSTGRES_DB=//p' "$ENV")
NEWDB="${OLDDB}_r$(date -u +%Y%m%d%H%M)"                          # never the live name; createdb refuses an existing one
echo "NEWDB=$NEWDB OLDDB=$OLDDB"
# Space: the database volume must hold the restored copy, the WAL of its transaction and a reserve (a value not read stops).
FREE_KIB=$($PF exec -T db sh -c 'df -Pk /var/lib/postgresql/data' | awk 'NR==2 {print $4}')
DB_BYTES=$($PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_database_size(current_database())"')
DUMP_BYTES=$(wc -c < "$BPATH/partflow.dump")
[ -n "$FREE_KIB" ] && [ -n "$DB_BYTES" ] && [ $((FREE_KIB * 1024)) -ge $((DB_BYTES + 2 * DUMP_BYTES + 1073741824)) ] \
    || { echo "not enough space (or it could not be read): expand the storage first, or follow path 4"; exit 1; }
$PF exec -T db sh -c 'createdb -U "$POSTGRES_USER" --template=template0 "$1"' sh "$NEWDB"
# A failed restore stops here with nothing restored (one transaction): drop the never-live copy, fix the cause, rerun B1:
#   $PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <NEWDB printed above>
$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --single-transaction --exit-on-error --no-owner --no-privileges' sh "$NEWDB" \
    < "$BPATH/partflow.dump"
```

Then edit `.env.production`: `PARTFLOW_RELEASE=<previous tag>`,
`POSTGRES_DB=<NEWDB printed by B1>` and `PARTFLOW_ACCEPT_SCHEMA_REVISION=`
(empty). B2 checks the restored database with the previous release:

```sh
REC=<release record directory>
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current" (otherwise it exits non-zero: B2 stops)
rc=0
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json" || rc=$?
echo "reconcile exit $rc"
```

The owner's approval is recorded in `$REC/rollback.json` before B3 (see above).
B3 starts the previous release on the restored database and switches `web` only
after `backend` reports it ready (S3 DV-8 order):

```sh
PREV=<previous tag>
ENV=.env.production
PF="docker compose -f compose.production.yaml --env-file $ENV"
PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV")
$PF up -d db backend                                              # db recreated for the new POSTGRES_DB (same volume)
# The web switch waits (up to 180 s) for backend to report the previous release with schema current.
ready=; i=0
while [ "$i" -lt 90 ]; do
    i=$((i + 1))
    body=$(curl -fsS --max-time 10 "http://127.0.0.1:$PORT/api/health" 2>/dev/null) || body=
    case $body in *"\"release\":\"$PREV\""*) case $body in *'"schema":"current"'*) ready=1; break ;; esac ;; esac
    sleep 2
done
[ -n "$ready" ] || { echo "backend did not report release $PREV with schema current: web not switched; stop backend and review"; exit 1; }
$PF up -d --no-deps web                                           # the reopen: backend first, then web
deploy/production/smoke.sh --release "$PREV"
```

`reconcile` must exit 0, or exit 1 with only findings that are in the release's
`pre-reconcile.json` (`deploy/production/reconcile_regression.py`).
`rollback.json` carries the §1 fields plus `approved_by` (the owner),
`approved_at` (UTC) and `reason`, the new and the old database names, the backup
name and both reconcile results. **The migrated database keeps its name and
content** for the path 4 analysis; the owner drops it after the observation window
(`$PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <old name>`), never a
tool.

### New-instance restore

For a host failure (§8) or a move (`SYNOLOGY_NAS.md` §10), on the new host with
the matching release checkout (the release that took the backup, at least
P16-S5) and a **new, empty** `postgres_data` volume. The copied backup directory
is checked first (`sha256sum -c SHA256SUMS` and `backup-verify`, as in step A).
Then, with the same `PF`, `BPATH` and space rule as step B1 (`DB_BYTES=0`):

1. `$PF up -d db`;
2. emptiness check: `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '"'"'public'"'"'"'` must print `0`, otherwise stop: the volume is not new;
3. the step B1 `pg_restore` line into the container's database, `$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' < "$BPATH/partflow.dump"` (a failure rolls back, the database stays empty, and the emptiness check passes again before a retry);
4. `$PF --profile ops run --rm -T db-roles`, then `… -T db-roles apply-grants`;
5. `revision` and `reconcile` as in step B2, with the report kept in the records directory;
6. the owner's approval recorded in `<records-dir>/<UTC>-restore-<NAME>/restore.json` (same fields as `rollback.json`), then `$PF up -d backend`, the step B3 health wait, `$PF up -d --no-deps web` and `deploy/production/smoke.sh --release <tag>`.

Document the data-loss window against the approved RPO.

Never assume `alembic downgrade` is safe. PartFlow intentionally protects
append-only history, and a downgrade may refuse or would discard newly supported
data.

## 7. Reconciliation

Reconciliation is the read-only backend command `python -m app.cli reconcile`
(Phase 16 slice 1). It runs the checks below in one read-only database snapshot
and prints one JSON report on stdout; it never repairs anything.

```bash
# development stack
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

- **pf-managed staging:** `pf` refuses `exec`/`run`, so run the raw Compose form
  of `SYNOLOGY_ADMIN.md` §14 outside the controller, never concurrently with
  `pf update`, `pf backup`, `pf reset-db`, `pf purge` or `pf restore-instance`.
  Write the report to the operator's home, never into a pf-managed directory:

  ```sh
  f="$HOME/partflow-reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
  sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo     PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<db>'     DEPLOY_ADMIN_INSTANCE_ID=<instance UUID from 'pf instances'>     docker compose     --project-directory /volume1/docker/partflow/repo     --env-file /volume1/docker/partflow/config/.env     -p partflow-staging     -f /volume1/docker/partflow/control/compose.nas.yaml     exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc"     || echo "could not run: no complete report (exit $rc)"
  ```

  Paths, project name and UUID are the instance's own (`SYNOLOGY_ADMIN.md` §14
  example values shown). All three variables are required; Compose refuses to
  start without them and writes no report. A wrong `DEPLOY_ADMIN_INSTANCE_ID`
  mislabels every resource Compose creates (§14); `exec` itself creates no
  container, volume or network.
- **Production:** run it from the release checkout with the running tag; it
  also runs while `backend` is stopped (`--no-deps` starts no other service,
  and the database must be up):

  ```sh
  f="reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
  $PF run --rm --no-deps -T backend python -m app.cli reconcile > "$f"; rc=$?
  ```

  then apply the same report check as above. The candidate-image form for check
  (j), before the switch of a release, is
  `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`
  (§5, Execute step 2).
- **Options:** `--check ID` (repeatable, `a` to `j`; the others are `skipped`),
  `--statement-timeout SECONDS` (1-3600, default 300), `--max-findings N`
  (1-10000, default 100 findings listed per check; `finding_count` stays full).
- **Exit codes:** `0` clean, `1` mismatch (at least one finding), `2` could not
  run or incomplete (an `error` check or a run-level error wins over findings).
  The exit status counts **only** when the report holds one complete JSON
  document whose `exit_code` equals it; an empty or unparseable report means
  "could not run" whatever the status (Compose itself exits 1 before anything
  runs when the service is stopped or the project is wrong).
- Keep the report in the deployment record (§1, "Smoke/reconciliation
  results").

Checks, each keeping the original requirement as its definition:

| Check | Requirement |
| --- | --- |
| (a) | current-position projections replay from non-reversed Movement history; |
| (b) | every active/closed flow has a valid conservation history, plus the SLICE1 §17 cross-row invariants except the audit-row invariant (enforced by the transaction protocol and covered by the existing API tests); |
| (c) | per-PN introduced quantity reconciles with active, stocked, scrapped, and reversed outcomes under the canonical rules; |
| (d) | Machine assigned quantities reconcile with flows currently on each Machine; |
| (e) | demand `released_quantity` derives from `RECEIVED` evidence; |
| (f) | demand `allocated_quantity` and Work Order `completed_at` reconcile with active allocation rows; an authorized beyond-demand correction (allocation rows recorded `exceeds_demand`, Phase 14 slice 5) is not reported as allocation beyond the requested quantity; |
| (g) | no retained Movement references a purged row; |
| (h) | no append-only table was mutated outside an approved archival/purge path; realized as a guard-integrity check: the guard triggers are present, enabled and unchanged, the guard functions' source is unchanged, `partflow_app` and `partflow_maintenance` hold exactly their grants (no UPDATE, DELETE or TRUNCATE on append-only history), the roles keep their safe attributes and no membership, PUBLIC and default privileges hold nothing, and `session_replication_role` is not set to `replica`. It cannot see a mutation made by a superuser who disabled a trigger or set `session_replication_role` in its own session and restored it (accepted limit, owner decision OD-16-09); |
| (i) | Hot list entries are active demand (the `DEPLOYMENT.md` §5 query); |
| (j) | canonical identity under the running interpreter and database: canonical PNs, case-insensitive Worker badges, the Asset Tag prefix rule, every canonical-form CHECK re-evaluated under the running database collation and ctype, the collation version, and index-independent duplicate probes of the identity keys (the platform-upgrade identity check). |

(g) reports `not_applicable` until Movement-history archival exists, and (h)
reports `not_applicable` only on a database without the PartFlow database roles
(development, test, staging); both are neutral for the exit code. In the
production stack (`DATABASE_ROLES_REQUIRED=true`) a missing role is a finding.

Repair mapping for check (h) findings (reconciliation never repairs; the commands below are the operator's, the owner decides):

- privilege codes on `partflow_app`, `partflow_maintenance` or PUBLIC, and schema, database and default-privilege codes for those grantees: `$PF --profile ops run --rm -T db-roles apply-grants`;
- role codes except `ROLE_OWNS_OBJECTS` (`ROLE_ATTRIBUTE`, `ROLE_MEMBERSHIP`, `ROLE_MISSING`) and a role-scoped `REPLICATION_ROLE_SETTING` (`<db>/<role>` or `*/<role>`): `$PF --profile ops run --rm -T db-roles`, then `… db-roles apply-grants`;
- a finding for another database role (any privilege, column, schema, database or default-privilege code — `PRIVILEGE_EXCESS`, `COLUMN_PRIVILEGE`, `SCHEMA_PRIVILEGE`, `DATABASE_PRIVILEGE`, `DEFAULT_PRIVILEGE` — whose grantee is a role other than PUBLIC, `partflow_app` and `partflow_maintenance`; an `apply-grants` or `migrate` refusal `foreign_grantor`): review who granted it and why, and revoke it as the owner or the grantor; `apply-grants` deliberately leaves it in place;
- `TABLE_UNCLASSIFIED` and `TABLE_MISSING` (and the `apply-grants` or `migrate` refusals `table_unclassified` and `table_missing`): a table created or dropped outside a migration is an incident (§8); the owner decides. `apply-grants` and `migrate` refuse until the stray table is removed (or a release classifies it), or the missing table is restored;
- trigger codes (`TRIGGER_MISSING`, `TRIGGER_CHANGED`, `TRIGGER_DISABLED`, `TRIGGER_ENABLE_MODE`), function codes (`GUARD_FUNCTION_MISSING`, `GUARD_FUNCTION_CHANGED`), `ROLE_OWNS_OBJECTS` (and the `role_owns_objects` refusal), a database-wide `REPLICATION_ROLE_SETTING` (`<db>/*`) and `REPLICATION_ROLE_ACTIVE`: an incident (§8); the owner decides.

Never repair by re-running or downgrading migrations. A run that raced a grant or trigger change can show a transient finding: rerun before acting.

Operating rules:

- One read-only snapshot; the command takes only `ACCESS SHARE` table locks,
  before the snapshot, and no row or advisory locks. It fails after 5 s waiting
  for a table lock (sooner when `--statement-timeout` is shorter), or at once
  when its lock request deadlocks with another session. Never run a migration
  concurrently, and run it off-peak.
- Platform-upgrade rehearsal for (j): for a Python/UCD upgrade, run
  `--check j` from the candidate backend image against the current database;
  for a glibc or PostgreSQL-image change, run the restore drill onto the
  candidate server, `deploy/production/restore-test.sh --backup <latest>
  --db-image <candidate image>` (§4; its full `reconcile` executes check (j) and
  the check (h) trigger definitions on that server), or, after an in-place image
  change of the same major version, `reconcile --check j` before writes reopen (a
  `COLLATION_VERSION_MISMATCH` is an owner decision). The owner decides on
  re-canonicalization before any upgrade.
- For a Work Order reported `not_completed_but_fully_allocated`, `expected` is
  the replay value, not a repair proposal; the owner picks the done date in the
  incident.
- A non-zero exit is an incident (§8). Never edit history or projections
  directly; the owner decides each repair.

Scheduled form (P16-S6; the daily schedule is installed on the pilot host in
P16-S7): `deploy/production/scheduled-reconcile.sh`, run daily at 04:00 from the
running release's checkout as the account that owns `PARTFLOW_BACKUP_DIR`. It
runs the full `reconcile` in the production stack, writes the report to
`~/partflow-monitoring/reconcile/<UTC timestamp>-reconcile.json` (`--reports-dir`;
directory 0700, report 0600), and applies the exit-code rule above through
`monitor_report.py`: it prints `RECONCILE clean|mismatch|error|could_not_run
<report>` and one `FAIL <check> <title>` line per failed check, and exits 0
(clean), 1 (mismatch) or 2 (could not run, including a report that is not
complete). It refuses to run while a release holds its lock or the backup lock
(exit 2), stops a run longer than `--max-runtime-minutes` (60) and never runs
concurrently with a migration. `last-result.txt` in the reports directory holds
the last run's lines. Every non-zero exit reaches the scheduler's failure
notification; the notification names only check ids, titles and counts.
**Report handling:** a report is mode 0600 and may contain Worker badge values
(check (j)). Keep reports like backups and never attach them to tickets or
emails.

Reconciliation is read-only by default. A mismatch creates an incident; it does
not trigger an automatic repair.

## 8. Incident response

### Suspected duplicate, lost, or uncertain write

- stop the affected workflow if quantity integrity may be at risk;
- preserve request time, station, user/worker, PN, flow, and
  `device_event_id`;
- correlate the backend log by `request_id`, `device_event_id`, PN, QuantityFlow,
  Scan Station and Worker (commands in §2) before retrying;
- inspect server result/history before retrying;
- retry only with the original idempotency key when appropriate;
- never edit Movement history directly;
- use the canonical Undo/correction workflow only after the committed state is
  known.

### Guard-integrity finding (reconcile check (h))

- freeze writes (`$PF stop backend`, §5) when a trigger or guard function changed, a trigger is disabled, or `session_replication_role` is set database-wide or is active: the ordinary guards then do not fire for any session, `partflow_app` included;
- for the setting, the owner runs `ALTER DATABASE <db> RESET session_replication_role` inside `db`;
- compare the triggers and functions with the migration source;
- the owner decides the repair and whether history must be verified against the last backup;
- run `reconcile` and the privilege probe (`DEPLOYMENT.md` §3.1) before reopening writes.

### Database or storage pressure

- block new writes before disk is exhausted (the write freeze, §5);
- preserve logs and metrics;
- the storage alert is a `disk_*` line of `check.sh` below 15 % free: `disk_data`
  measures the database volume, `disk_backup` the backup directory, `disk_docker`
  the Docker root (images and container logs) and `disk_archive` the archive
  directory;
- do not delete PostgreSQL files, volumes, Movement rows, or backups ad hoc;
- backups need `2 x` the newest dump plus a reserve free in the backup directory,
  a path 3 restore needs the database size plus `2 x` the dump plus 1 GiB free on
  the database volume (§6), and a stale backup lock (`backup_lock_stale`) blocks
  backups and releases until it is removed (§3);
- expand storage or follow the Phase 16 verified archive/purge maintenance path;
- run reconciliation before reopening writes.

### Host failure

- prevent split-brain: confirm the failed instance cannot accept writes;
- provision the approved recovery host;
- restore the latest verified backup and matching release with the new-instance
  restore (§6);
- run reconciliation and smoke tests;
- document data-loss window against the approved RPO;
- redirect clients only after approval: the owner's approval is recorded in
  `restore.json` before `backend` starts.

## 9. Routine schedule

| Frequency | Tasks |
| --- | --- |
| Continuous | `check.sh` every 15 minutes (P16-S6; scheduled on the pilot host in P16-S7): health, restart, disk, certificate, backup-age, and error alerts with the OD-16-11 thresholds: backup older than 26 h, disk free below 15 %, certificate expiring within 21 days, any restart-count increase, any backend error record (the archival proposal arrives with P16-S10). The notification is the scheduler's failure notification; an unchanged failure is repeated every 6 h |
| Daily | The scheduled `backup.sh --kind daily --keep-daily 14 --keep-weekly 8` (the owner's values); review the scheduler's and the platform tool's notifications (exit 4 includes a daily that fails verification) and off-site replication; review critical errors; `scheduled-reconcile.sh` at 04:00 (after the 02:00 backup of the platform guides); review `last-check.txt`, `<reports-dir>/last-result.txt` and the notifications |
| Weekly | Review capacity trend and database growth (`growth.tsv` and `docker stats --no-stream`), failed logins/authorization events (`$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'`: `refusal.type` and `refusal.code` tell a sign-in refused or locked, a permission denied, `station_device_required` or `station_device_mismatch`, and `csrf_rejected` apart; the `app.application.authentication` sign-in lines name a user id only), and pending security updates (a host item, P16-S7) |
| Monthly | Prune old reconcile reports by hand after review (no tool deletes them; they may hold badge values); patch in staging then production; review users/roles, firewall rules, secrets, and runbook contacts; review database roles with `reconcile --check h`; remove pre-release and manual backups whose observation window ended, and daily backups reported invalid by rotation after review (never an archive) |
| On role-password rotation | A short write freeze: replace the role file, `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, `$PF up -d --force-recreate --no-deps backend`, then check health. A plain `up -d backend` does not pick up the new password (the container is not recreated), and running `db-roles` while the backend serves makes its new connections fail. Owner password: `ALTER ROLE … PASSWORD` inside `db` first, then replace `postgres_password` (no service restart: only the one-shot `migrate` and `db-roles` use it) |
| Quarterly or after material schema change | `restore-test.sh --backup <latest daily>` (§4), its timings compared with the RTO, a measured RPO/RTO exercise, and reconciliation review |
| Before a PostgreSQL-image or host glibc change | `restore-test.sh --backup <latest> --db-image <candidate>` (§7) |
| Before every release | `release.sh` takes the verified pre-release backup (§5); migration review, rollback decision, and smoke-test plan |

The organization must set actual RPO, RTO, retention, and owners. Examples in
this runbook are procedures, not service-level commitments.

