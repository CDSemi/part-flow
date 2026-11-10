# PartFlow Operations Runbook

> **Language:** English is the source of truth. [Tiếng Việt](./OPERATIONS_RUNBOOK.vi.md).

This runbook is the operator's procedure book for the PartFlow production stack: what to record for every release, how
to check health and find a problem, how to take and test backups, how to release and roll back, how to run the
reconciliation, what to do in an incident and what to do on a routine schedule. It is written for the person who runs
PartFlow on the company Synology NAS; that person is both the operator and the approver. Read
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 (the production stack) and §5 (what go-live needs) first, and
[`SYNOLOGY_NAS.md`](SYNOLOGY_NAS.md) §8 for the installation on the NAS.

**Status.** Every command in this runbook exists in the repository and has been run on Docker Desktop and in a Linux
container, but not yet on a Synology NAS. The schedules (the daily backup, `check.sh` and the scheduled
reconciliation) are recommended after go-live and never block use (`DEPLOYMENT.md` §5).

**Conventions used in every section.**

- `PF` is a shell variable that you set in the release checkout before running the commands:

  ```bash
  PF="docker compose -f compose.production.yaml --env-file .env.production"
  ```

  It is not the Deployment Admin tool `pf`. Every `$PF` command and every `deploy/production/*.sh` script runs from
  the repository root of a release checkout: the **running** release's checkout for daily work, the **candidate**
  release's checkout during a release.
- Run the commands as the account that owns the backup directory (`PARTFLOW_BACKUP_DIR` in `.env.production`). That
  directory has mode 0700, so a one-off container that reads it is started with `--user "$(id -u):$(id -g)"`;
  without that option the container cannot read it.
- `<partflow-host>` is the host name users type in the browser, `<backup-dir>` is `PARTFLOW_BACKUP_DIR`, and
  `<records-dir>` is the deployment records directory (default `$HOME/partflow-deployments`).
- Times are Coordinated Universal Time (UTC). Reports and records are JavaScript Object Notation (JSON) documents.

## 1. Required deployment record

Every release and every manual operation (for example a rollback) leaves a record. `deploy/production/release.sh`
writes it as `record.json`, together with the output of every step, in `<records-dir>/<UTC>-<tag>/`; a manual
operation appends its files to the same directory with the same fields. The restore-drill evidence (`evidence.json`,
§4), the path 3 `rollback.json` and the new-instance `restore.json` (§6) are written under the same records
directory.

| Field | `record.json` key |
| --- | --- |
| Environment and URL | `environment`, `url` (options `--environment`, `--url`) |
| Host | `host` (the host name) |
| Release tag, commit and images | `release`: `tag`, `commit`, `previous_tag` and the local image IDs of `backend` and `web` |
| Alembic revision before and after | `alembic`: `before`, `after`, `expected` |
| Operator and approver | `operator`, `approver` (one person may give the same name to both) |
| Start and end time (UTC) | `started_at`, `finished_at` |
| Pre-release backup: path, checksum and verification | `backup`: `kind`, `reference`, `name`, the absolute `path`, `verified`, `verification`, `freshness`, `taken_by` (`release.sh`, `operator` or `null`), `manifest_sha256` and the manifest facts of §3 |
| Migration output, grants report and database-role reports | `migration`: the files `migrate.json` (it carries the `grants` result) and `migrate.log`; the JSON reports of a manual `provision-roles` or `apply-grants` run are kept beside them |
| Smoke and reconciliation results | `reconcile`, `smoke` |
| Rollback deadline and observation owner | `rollback_deadline`, `observation_owner` (options `--rollback-deadline`, `--observation-owner`) |
| Known limitations | `known_limitations` (option `--known-limitations`) |
| How the run ended | `freeze_completed_at` (the instant `backend` was confirmed stopped; `null` when there was no freeze), `outcome`, `writes_reopened_at`, `refrozen` |

## 2. Health and diagnosis

### Quick checks

On the development stack (`compose.yaml`):

```bash
docker compose ps
docker compose logs --since=15m backend frontend db
curl --fail --silent --show-error https://<partflow-host>/api/health
```

On the production stack:

```bash
$PF ps
$PF logs --since=15m backend web db
curl --fail --silent --show-error https://<partflow-host>/api/health
curl --fail --silent --show-error https://<partflow-host>/api/health/live
$PF run --rm --no-deps -T backend python -m app.cli revision
```

`/api/health` is readiness. It reports `release`, `commit`, `schema` (`current`, `accepted`, `mismatch` or `unknown`),
`expected_revision`, `database_revision` and `accepted_revision`, and answers 503 on a schema mismatch or when the
database cannot be reached. `/api/health/live` is liveness only, and the container health checks use it, so a
container stays healthy while readiness is 503 during a schema mismatch. `revision` prints the same facts from a
one-off container and also works while `backend` is stopped.

### Host checks and the `status` command

Run these from the running release's checkout:

```bash
# manual diagnosis: the same checks as the scheduled run, without touching its state
deploy/production/check.sh --url https://<partflow-host> --no-state
# database size, connections, lock waits, Movement rows and size, schema readiness, backup age
$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status
docker stats --no-stream
```

**Always add `--no-state` to a manual `check.sh` run.** Without it the manual run advances the error cursor and the
restart baseline, so the next scheduled run would miss the errors and restarts the manual run already saw.

`check.sh` prints one line per check, `PASS|FAIL|SKIP <id> <reason>`, in this order, followed by optional `NOTE`
lines:

| Check | Fails when |
| --- | --- |
| `https` | `/api/health` does not answer 200 with `schema` `current` and the release of `.env.production` |
| `certificate` | the certificate expires within 21 days (`--min-cert-days`) |
| `containers` | there is not exactly one running, healthy `db`, `backend` and `web` |
| `restarts` | a restart count increased since the previous full run |
| `errors` | the backend wrote an `ERROR` or `CRITICAL` record since the previous full run (up to three request ids are shown) |
| `disk_data` | the database volume has less than 15 % free (`--min-free-percent`) |
| `disk_backup` | the backup directory has less than 15 % free |
| `disk_docker` | the Docker root (images and container logs) has less than 15 % free |
| `disk_archive` | the archive directory has less than 15 % free; `SKIP` while no archive directory is configured (`--archive-dir`) |
| `database` | the `status` run could not run, did not answer within 120 s, or reported a database error |
| `schema` | `status` reports the schema not ready (`schema_not_ready`) |
| `backup_age` | no published backup is found, the backup directory cannot be read, or the newest published backup of any kind completed more than 26 hours ago (`--max-backup-age-hours`) |
| `archival_proposal` | always `SKIP`: Movement-history archival is not implemented yet |

The last four come from one read-only `status` run. While a release holds its release lock, or `release.sh` holds the
backup lock, these four are `SKIP`. Once such a lock is older than 4 hours the run reports `FAIL database` naming the
lock instead, because a killed `release.sh` leaves its lock behind; remove it as the Backup lock part of §3 describes.
Schedule `check.sh` together with the daily backup: without daily backups, `backup_age` fails as soon as the last
backup is older than 26 hours.

Exit codes: 0 nothing to notify, 1 at least one `FAIL` to notify, 2 the check could not run (this also notifies). An
unchanged set of failures is notified again only after 6 hours (`--renotify-hours`); the output then says
`already reported`. `deploy/production/check.sh --help` prints every option, including `--cacert` for a private
certificate authority (CA) and `--resolve-to` when the host cannot resolve its own name.

`check.sh` keeps its state in `~/partflow-monitoring` (`--state-dir`, mode 0700): `restarts` (the restart baseline),
`errors-since` (the log cursor), `alert-state` (re-notification), `last-check.txt` (every run), `status.json`,
`status.err` and `growth.tsv` (one dated line per day: database bytes, Movement rows and Movement bytes).

The `errors` check reads the log of the current `backend` container only, and Docker deletes a container's log with
the container. When `backend` was recreated since the previous full run (by a release or a role-password rotation),
the run adds `NOTE errors backend was recreated ...`, and the records of the replaced container after the cursor can
no longer be read. **Before you recreate `backend` by hand**, run
`deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` and keep what it reports.

`status` prints one JSON document whose `result` is `ok`, `attention` or `error`, with exit code 0, 1 (a finding such
as `backup_stale` or `schema_not_ready`) or 2 (`configuration_invalid`, `database_unavailable`, `lock_timeout`,
`statement_timeout`, `database_error`, `internal_error`). `database.locks` holds `waiting` and `longest_wait_seconds`;
this lock-wait figure is reported, never a threshold. `status` runs one read-only transaction as the application role
with a 5 s lock timeout. Keep its `--statement-timeout` (default 20 s) below 30 s so that it never aborts a release's
`migrate`.

### Logs and the HTTP request id

In the production stack the backend writes one JSON object per line: `ts`, `level`, `logger`, `message`,
`request_id`, then the record's own fields. Every response, refusals and failures included, carries the **HTTP request
id** in the `X-Request-ID` header. A client's own value is kept when it is 1 to 64 characters of `A-Za-z0-9._-`;
otherwise the backend generates one. Every backend record of that request carries it as `request_id`. To find a
request:

```bash
$PF logs --no-log-prefix backend | grep '"request_id":"<id>"'
```

The HTTP request id changes on every resubmit. A production command is therefore always correlated and retried by its
`device_event_id` (the "request identity" in the user interface), never by `request_id`. To follow a command, a Part
Number (PN) or a Scan Station, filter the `app.access` records by the fields they carry:

```bash
$PF logs --no-log-prefix backend | grep '"device_event_id":"<id>"'
$PF logs --no-log-prefix backend | grep '"part_number":"<PN>"'
$PF logs --no-log-prefix backend | grep -E '"quantity_flow_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"(area|source_area|target_area|starting_area)_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"work_order_demand_id":<n>[,}]|"work_order_demand_ids":\[([0-9]+,)*<n>[],]'
$PF logs --no-log-prefix backend | grep '"station_id":"<id>"'
```

A numeric id is matched together with its closing `,` or `}` so that `1` does not also match `10`. An Area is also
matched as the source, target or starting Area of a command, and a Work Order Demand also inside the
`work_order_demand_ids` list of an allocation.

An `app.access` record holds `event`, `method`, `route` (the route template; `path` only when no route matched),
`status`, `duration_ms`, `client`, `outcome` (`ok`, `created`, `replayed`, `refused` or `error`), `slow`, `refusal`
(`type`, `code`, `message`) and `context`: the PN, QuantityFlow, quantity, Area, Operation, Machine, Work Order, Work
Order Demand (an allocation's lines as `work_order_demand_ids`), `device_event_id`, Scan Station, `user_id` and
`worker_id` that the request named. `worker_id` appears only on records of a command that reached identity resolution
(a created command or an identity refusal); the Worker of a replayed command is in its Movement.

What is logged and at which level:

- Never logged: request or response bodies, query strings, cookies, device tokens, cross-site request forgery (CSRF)
  tokens, badges, passwords, scanned values and PostgreSQL `DETAIL` lines (the database runs with
  `log_error_verbosity=terse`). The one exception is the first-run setup token, which is printed once, until setup is
  complete.
- Routine reads faster than 1 second and every health poll are not written at `INFO`; `"slow":true` marks a read of
  1 second or more.
- Designed refusals (`not_ready`, `release_mismatch`, `password_check_busy`, a rejected request) are `INFO`; only a
  real failure is `ERROR`.
- `web` writes one JSON edge record per request: client address, method, path without query string, status, bytes,
  duration, upstream status, user agent and the same `request_id`. Neither `web` nor the backend logs query strings,
  cookies or PartFlow headers.

A 502 or 504 JSON answer comes from `web` (`server_unavailable`) and means that the outcome of a write is unknown. Find
it by its `request_id` in the `web` log, and resolve the write with its original `device_event_id`. **Do not retry a
timed-out production command with a new `device_event_id`** until the original result is known; retry only with the
original idempotency key, so that an uncertain answer can never duplicate a write.

### Log retention

Each container keeps at most 5 json-file logs of 10 MB. How many days that holds depends on the load:

- Station commands: about 1.1 MB of backend log and 0.6 MB of `web` log per 1,000 commands, measured with synthetic
  load as `docker compose logs` output.
- Polling displays: every open Production Board, Area Board or Tracking screen refreshes every 15 s, and each request
  is a `web` record. The Production Board sends 2 requests per refresh, about 11,500 records or roughly 3 MB of `web`
  log a day per open board. The backend writes these reads only when they take 1 second or more.
- On disk the json-file driver wraps and escapes every line, so a file holds fewer records than the figures above.

Commands alone (5,000 a day) would give about 9 days of backend log and 18 days of `web` log; with three Production
Boards open all day the `web` log holds only a few days. **Copy what an investigation needs as soon as it starts.**
Measure the real figures on the NAS, with its real displays.

### Database permission errors

In the production stack the backend connects as the database role `partflow_app`. A `permission denied` (SQLSTATE
42501) in the backend log means a code path tried to change protected history or lacks a grant. It is an incident
(§8) and is never fixed by granting more. Run `reconcile --check h` (§7) before anything else.

### What else to check

- host CPU, memory, disk, disk input/output, clock and any recent reboot;
- container restart counts and health status;
- the reverse proxy and the certificate expiry;
- PostgreSQL connections, locks, storage growth and backup age;
- the connectivity state in the browser, and whether writes are correctly blocked;
- errors correlated by PN, QuantityFlow, Area, Operation, Machine, Worker, Scan Station and `device_event_id`.

## 3. Logical database backup

`deploy/production/backup.sh` takes the backup. Run it from the repository root of a release checkout (the running
release's for scheduled and manual backups; `release.sh` calls it from the candidate's) while the production stack's
`db` runs. The dump is streamed from **inside `db`**
(`pg_dump --format=custom --no-owner --no-privileges --lock-wait-timeout=60s`, one snapshot, with the client and the
server being the same binary), listed with `pg_restore --list`, and published with a manifest and checksums by the
`backup-tools` one-shot service, which has no network and sees only the backup directory. The repository provides the
backup, its verification and the retention; encryption and the off-host copy belong to the platform's backup tool
(below).

### Artifact and name

One backup is one directory `<backup-dir>/<NAME>/`:

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-daily
                                         and 20261008T140000Z-pre-release-v1.0.0-rc.2
```

`kind` is `daily`, `manual` or `pre-release`. `label` is the target release tag: required for `pre-release` and
refused otherwise. The stamp is the UTC start of `backup.sh`. The directory holds exactly `partflow.dump`,
`partflow.dump.list` (the `pg_restore --list` output), `manifest.json` and `SHA256SUMS`. It is assembled under
`<backup-dir>/.partial/` and published by one atomic rename, so a backup either exists completely or not at all; a
crash leaves only `.partial/` entries. Entries whose names do not follow this pattern (for example a DiskStation
Manager (DSM) `@eaDir` folder) are ignored and reported, never fatal.

### Taking a backup

```bash
# scheduled daily backup (14 and 8 are placeholders for the owner's retention)
deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
# manual backup (a reason is required)
deploy/production/backup.sh --kind manual --operator "<name>" --reason "<why>"
```

`release.sh` takes the `pre-release` backup itself (§5); the manual form is
`backup.sh --kind pre-release --label <target tag> --tools-release <target tag> --operator "<name>"`. On success
standard output is exactly one line, `BACKUP <NAME> <absolute path>`; progress and the tool reports go to standard
error. `deploy/production/backup.sh --help` prints every option:

| Option | Meaning |
| --- | --- |
| `--kind daily\|manual\|pre-release`, `--operator NAME` | required |
| `--reason TEXT` | required for `manual`; defaults to `scheduled daily backup` (daily) and `pre-release backup for TAG` (pre-release) |
| `--label TAG` | the target release tag; required for `pre-release`, refused otherwise |
| `--tools-release TAG` | the `backup-tools` image that writes and verifies the manifest (default: `PARTFLOW_RELEASE` of the env file) |
| `--keep-daily N --keep-weekly N` or `--no-rotate` | `daily` only, and one of the two is required: the owner's retention (below); there is no default |
| `--reserve-mib N` | free space kept beyond twice the newest dump (default 1024) |
| `--lock-held-by-release DIR` | for `release.sh` only (it holds the backup lock) |
| `--rehearsal --project NAME` | a throwaway Compose project (never `partflow-production`) |

Text values are 1 to 500 characters without control characters, `"` or `\`. The steps, in order, each reported as
`backup: <step> ok (<ms> ms)` on standard error:

1. `preflight`: the tools, the env file (`PARTFLOW_RELEASE`, `PARTFLOW_BACKUP_DIR`), the Compose `config`, `db`
   running, the `backup-tools` image and the backup lock (below);
2. `identify`: the running release, commit, image IDs and the expected Alembic revision (best effort, never fatal);
3. `space`: the backup directory must have free at least twice the newest dump (or the database size when there is no
   dump yet) plus the reserve; otherwise the backup is refused and nothing is written;
4. `dump`, `list` and `revision`: the three calls inside `db`, run with `TZ=UTC` because the archive stores
   local-time fields;
5. `manifest` (`backup-manifest` publishes the directory), `verify` (`backup-verify` checks it), `rotate`
   (`backup-rotate`, for a `daily` backup only), then `done`.

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | completed | nothing |
| 1 | refused before writing (`backup_running`, `backup_lock_stale`, `insufficient_space`, `name_exists`) | read the printed reason; nothing was written |
| 2 | could not run (usage, tools, environment, `db` not running, the backup lock cannot be created) | fix the cause and run it again |
| 3 | failed: no backup was published, or the published backup failed verification | never use a backup the message names; remove it after review |
| 4 | the backup is complete and verified, but the rotation failed or found a daily backup that fails verification | review the `backup-rotate` report on standard error (see Retention below) |

When the daily backup is scheduled, the host scheduler's failure notification reports every non-zero exit.

### Manifest and verification

`manifest.json` (`manifest_version` 1) holds the deployment record facts of §1 and what a restore needs:

| Record field | Manifest key |
| --- | --- |
| UTC timestamp | `dump_started_at`, `completed_at` |
| Environment, host | `environment`, `host` |
| Git commit and images | `release.commit`, `release.tag`, `images.backend`, `images.web`, `images.db` |
| Alembic revision | `alembic_revision` (and `alembic_rows`); `release.expected_revision` is what the running release expected |
| PostgreSQL major version | `database.server_major` (and `server_version`, `pg_dump_version`, `database.name`) |
| Dump and list checksums | `files[].sha256` and `SHA256SUMS` |
| Operator and reason | `operator`, `reason` |

It also records the `kind`, the `label`, the `tool` image that wrote it and the `dump` facts (`options`,
`toc_entries`, `table_data_entries`, `tables_checked`, `extra_tables`). `tables_checked: true` means that every table
of the release's table classification has a data entry; a dump of another revision is published with
`tables_checked: false`. A database whose `alembic_version` does not hold exactly one valid revision, or that has
tables the release does not know, is still backed up and published with a warning, because a faithful backup of a
drifted database is worth more than none; but `migrate` refuses such a backup (§5) and the restore drill fails at its
revision step.

Verify any backup, read-only, with the same checks that `migrate` and the drill run, and independently of any image
after every copy:

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"
(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)
```

Prefix the first command with `PARTFLOW_RELEASE=<tag>` to use another release's `backup-tools` image. `backup-verify`
prints one JSON document with the checks `directory`, `files`, `sha256sums`, `manifest`, `dump_header`, `list` and
`expect_database` (option `--expect-database NAME`). Its exit code is 0 `verified`, 1 `invalid` (`backup_invalid`:
**do not use the backup**) or 2 `failed`.

### Backup directory

- It is an existing absolute directory outside the repository checkout, the secrets directory and any archive
  directory. It holds **production backups only**, never staging's or a drill's. Its mode is 0700, it is owned by the
  account that runs `backup.sh` and `release.sh`, and its files have mode 0600. Create it before the first `$PF`
  command, because every Compose command of the stack needs `PARTFLOW_BACKUP_DIR` (`DEPLOYMENT.md` §3.1).
- A dump holds every table, including password hashes and session and device token digests. **Treat the directory
  and every copy of it as secret.**
- Keep free space of twice the newest dump plus the reserve (default 1 GiB).

Dumps carry no grants by design (`--no-privileges`): after a restore, the database roles and their privileges are
derived again from the code (§4).

### Retention, off-host copy and alerts

`backup.sh --kind daily` applies `backup-rotate`. It can also be run alone; `--dry-run` reports without deleting:

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-daily 14 --keep-weekly 8 --dry-run
```

It keeps the newest `--keep-daily` verified daily backups, plus the newest daily backup of each of the newest
`--keep-weekly` calendar weeks (ISO 8601 weeks, UTC; the weeks overlap the daily window, as with restic
`forget --keep-daily --keep-weekly`). Every candidate is verified first; one that fails verification is not counted
and is **never deleted** (exit 4: the operator reviews it, §9). **Pre-release and manual backups are never rotated**:
the operator removes them after the observation window (§9). **No tool ever rotates or prunes an archive directory**,
platform tools included. Leftovers in `.partial/` older than 24 hours are removed. The values 14 and 8 are
placeholders: the owner sets the real values in the scheduled command; they are required options, never code
defaults.

Encrypt the backup directory and copy it off the host with the platform tool, never by hand: Hyper Backup with
client-side encryption to a target outside the NAS on Synology (`SYNOLOGY_NAS.md` §6), restic to object storage on a
virtual private server (`VPS.md` §8).

Alerts, once the schedules are installed:

- a failed scheduled backup (any non-zero exit, including a daily backup that fails verification) raises the host
  scheduler's failure notification;
- a failed off-host copy raises the platform tool's own notification;
- "backup too old" is the `check.sh` check `backup_age`: the newest published backup of any kind is older than 26
  hours (§2).

Review these notifications daily (§9). With a daily backup the recovery point objective (RPO), the most recent work
that can be lost, is 24 hours. Until the daily backup is scheduled, the only backups are the ones `release.sh` takes
before each release and any manual backups.

### Backup lock

Backups are serialized by the directory `<backup-dir>/.backup.lock`. Its `owner` file holds `host`, `pid`,
`started_at`, `by=backup.sh|release.sh` and `name` or `release`. A second run is refused, never queued:
`backup_running` means that a backup, or a release holding the lock, is in progress, so wait. `release.sh` holds the
lock from the write freeze (or from its backup step) through the `backend` switch. **Schedule daily backups outside
maintenance windows**: a daily backup that is running stops a release before the freeze with nothing changed, and a
daily backup that starts during a release is refused.

A lock that cannot be created at all (the backup directory is full, out of inodes or read-only) is reported with the
`mkdir` error as could-not-run (exit 2), never as `backup_running`; `release.sh` then stops with `could_not_run`
before the freeze, with nothing changed.

`backup_lock_stale` means that the lock was left by a run that no longer exists (it was killed, killed by the
out-of-memory handler, or the power failed). **It is never broken automatically.** Recover by hand:

```bash
cat "<backup-dir>/.backup.lock/owner"   # host, pid, started_at, by and name|release
ps -p <pid>                             # on that host: must report no such process
$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"'   # must print 0
rm -r "<backup-dir>/.backup.lock" "<backup-dir>/.partial/<name>"   # the name from the owner file, when it has one
```

**Never remove a lock whose owner is `release.sh` while that release runs.** While `release.sh` holds the backup lock
(or its release lock exists), `check.sh` skips its `status` checks (§5 Observe); once such a lock is older than 4 hours
`check.sh` reports `FAIL database` instead (§2). A release lock left by a killed `release.sh` is the empty directory
`<records-dir>/.release.lock`: when no `release.sh` runs (check with `ps`), remove it with `rmdir`.

## 4. Restore test — never overwrite first

Restore into an isolated database or an isolated stack, **never directly over the only production database**. The
procedure has nine steps:

1. verify the dump and manifest checksums;
2. provide the same PostgreSQL major version, or a documented compatible target;
3. create an empty restore-test database;
4. restore with `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges` (one transaction, so a
   failed restore leaves the target empty);
5. start the matching application release against that database;
6. verify the Alembic revision;
7. run health, representative read models, the quantity, Movement and allocation reconciliation, and the designated
   smoke tests;
8. record the restore duration and the result;
9. destroy the isolated copy only after the evidence is kept.

A dump contains no grants, so a restored database has none until `apply-grants` derives them, and the application role
cannot work without them. In a restore into a new cluster, therefore, provision the database roles first
(`provision-roles`; a restore drill uses throwaway passwords) and run `apply-grants` against the restored database
before starting the application (step 5). **Reconcile check (h) must pass before a restored database serves
production**: path 3 and the new-instance restore (§6) run `reconcile` before `backend` starts.

### The restore drill script

On the production host the whole procedure is `deploy/production/restore-test.sh`, run from the repository root of a
release checkout:

```bash
deploy/production/restore-test.sh --backup <NAME> --operator "<name>"
# a candidate PostgreSQL image (glibc or image change), a baseline of known findings:
deploy/production/restore-test.sh --backup <NAME> --operator "<name>" \
    --db-image postgres:16.14-bookworm --baseline-report <records>/<release>/pre-reconcile.json
```

`deploy/production/restore-test.sh --help` prints every option: `--release TAG` (the release to run on the restored
database; the default is the release in the manifest, and a backup taken between a migration and the rewrite of the
env file needs it named), `--tools-release TAG`, `--db-image IMAGE`, `--baseline-report FILE`, `--project`,
`--http-port`, `--edge-subnet`, `--records-dir`, `--space-factor` and `--keep`.

The script enforces isolation. The drill runs in its own Compose project `partflow-restore-<suffix>` (never
`partflow-production`) with its own volume and networks, its own loopback port and edge subnet (neither the production
ones), the fixed database name `partflow_restore_test`, **generated throwaway database-role passwords** (the secret
files are removed at teardown), one backend worker, and the backup directory mounted **read-only**. It never runs
`migrate`, never writes to the backup directory, and refuses a project that already has containers or volumes. It
needs free space of 5 times the dump plus 1 GiB on the Docker root (`--space-factor`).

The script performs the nine steps and records each one in the evidence:

| Evidence step | Procedure step |
| --- | --- |
| `verify` | 1: `backup-verify` |
| `db_start` | 2 and 3: an empty database on the PostgreSQL image that the checkout's `compose.production.yaml` pins, or `--db-image`; the evidence compares its major version with the manifest's |
| `restore` | 4, in one transaction |
| `roles`, `grants` | `provision-roles`, then `apply-grants`, before the application starts |
| `revision` | 6: state `current` and the manifest's revision |
| `app_start` | 5 and the health part of 7: `backend` and `web` up, readiness `current` |
| `reconcile` | the reconciliation part of 7: the full `reconcile`, whose checks (a) to (f) replay Movement history against every stored projection and allocation |
| `smoke` | the smoke part of 7: `deploy/production/smoke.sh` |
| `evidence` | 8 |
| `teardown` | 9: `down -v` of the drill project only, after the evidence file exists, and only when this run created the project; `--keep` keeps it and prints the command |

Pass rule:

- `passed` needs reconcile exit 0 **and** check (h) `pass` **and** check (j) `pass`. Grants are freshly applied in
  the drill, so a check (h) finding or `not_applicable` is a real defect. With `--db-image`, check (j) is the glibc
  (GNU C library) and PostgreSQL-image half of the platform-upgrade identity check (§7).
- `passed_with_preexisting_findings` needs `--baseline-report`, every finding (check (j)'s included) present in that
  report (`reconcile_regression.py` compares them), and check (h) `pass`.
- Anything else is `failed`.

The evidence file `<records-dir>/<UTC>-restore-test-<NAME>/evidence.json` records the outcome, the failed step, the
backup and drill facts, the drill server's version, collation and collation versions, the PostgreSQL major versions of
the backup and of the drill server with `server_major_match` (`false` is also printed as a warning, because it is not
a same-major drill; `null` when either is unknown), the timing of each step (`restore_to_ready` and `total` measure how
long a restore takes) and the reconcile statuses.

| Exit | Meaning |
| --- | --- |
| 0 | `passed` or `passed_with_preexisting_findings` |
| 1 | `failed`, or not enough free space |
| 2 | could not run, or interrupted (nothing created is left running unless `--keep`) |
| 3 | teardown failed: the evidence stands, and the message prints `docker compose -p <project> down -v` |

**Known limitation:** reading back individual read-model screens with a signed-in account is not automated, because it
needs a real account of the restored data; the evidence states `read_model_readback: not_automated`. To check it by
hand, run the drill with `--keep`, sign in on the printed loopback port, and tear the project down with the printed
command afterwards.

Use explicit restore-test names. **Never put the production database name into a drill command.**

## 5. Release and migration

### Before maintenance

- Choose the exact release tag and know what it changes.
- Confirm that continuous integration (CI) passed for that commit.
- Review every migration of the release and how it would be recovered, and estimate its lock time, duration and disk
  use on data of realistic size.
- Check that the newest backup is recent (§3) and, when an off-host copy is configured, that its last notification
  reported success. `release.sh` takes its own verified pre-release backup in any case: **inside the write freeze**
  when a migration is pending (so there is no gap between the backup and the migration), otherwise before the switch.
- Confirm that the previous release's images are still on the host: rollback path 1 needs them (§6), and
  `release.sh` refuses to start without them.
- Run the reconciliation (§7) on the current release, keep the report, and treat every finding as an incident (§8).
  `release.sh` does this itself. Run it, and any recovery command, with the **current** tag, before the candidate tag
  is written to `.env.production`; the candidate tag lives only in the shell until the switch.
- Decide whether writes must stop. A pending migration always needs the write freeze below.
- Tell the users when the window starts and when you will decide on a rollback, and ask the stations to finish their
  open dialogs.
- Schedule daily backups outside maintenance windows: a running backup stops a release before the freeze with nothing
  changed (`backup_running`, §3), and a daily backup that starts during a release is refused.

### Write freeze

The write freeze is **stopping `backend`** (`$PF stop backend`); there is no other maintenance mode.

- `uvicorn` finishes the requests in flight, an import included, and the stop waits up to 200 s. With more than one
  worker the supervisor keeps the socket open until the last worker exits, so requests sent meanwhile hang and end as
  a 504 or 502 (`DEPLOYMENT.md` §3.1, Process model). **Start the freeze when no import is running.**
- While `backend` is stopped, `web` still serves the application shell and answers `/api/*` with its JSON 504 and then
  502. Every client shows the OFFLINE banner within about a second, and every write control is blocked.
- Open dialogs keep their drafts. A write that raced the stop has an unknown outcome; it keeps its `device_event_id`
  and is retried with it after a reopen on the **same** release. After a reopen on a **new** release the old page
  cannot send it (it is refused with 409): the operator reloads the page and checks the Area or Work Order before
  repeating the action.
- An import that was cut off leaves only whole, committed Work Orders.
- `reconcile`, `revision`, `migrate` and the backup run from one-off containers against `db` while the stack is
  frozen.
- **Reopen** with `$PF up -d backend` on the same release, and only after the checks of the steps below have passed.
  At a release switch the reopen is the `web` switch.

### Execute

Run `release.sh` from the repository root of the release checkout, with the release tag checked out:

```bash
deploy/production/release.sh --release <new-tag> --operator "<name>" --approver "<name>"
```

`deploy/production/release.sh --help` prints every option. The tag has the form `vMAJOR.MINOR.PATCH`, optionally
followed by `-alpha.N`, `-beta.N` or `-rc.N`. Backup options:

- With neither backup option, `release.sh` takes the verified pre-release backup itself.
- `--pre-release-backup <NAME>` names an existing `backup.sh` backup. It is allowed only for a release **without** a
  pending migration; with one, `release.sh` stops with nothing changed, because the backup must be taken inside the
  freeze.
- `--no-backup-reason "<text>"` is only for a first installation or a rehearsal, and the reason is recorded.

`release.sh` runs and records these steps:

1. preflight: the tools, the environment file, the tag form and clean build inputs. It also refuses, with nothing
   changed, while `partflow_app_password`, `partflow_maintenance_password` or `postgres_password` is missing, empty
   or not a regular file, while a `partflow-staging` Compose project exists on the same Docker daemon, and while the
   running release's images are missing;
2. the current revision, and the pre-release reconciliation with the running release;
3. the candidate build: an existing tag is never rebuilt, and it is reused only when both images were built from this
   commit as this release;
4. the candidate's check (j) and its `revision`, which must report this release and commit;
5. the write freeze, when a migration is pending;
6. the verified pre-release backup (step `pre_release_backup`: `backup.sh --kind pre-release`), after the freeze when
   a migration is pending; the backup lock is held from the freeze through the `backend` switch;
7. `migrate`, which verifies the backup first and also applies the grants;
8. the post-release reconciliation;
9. the `backend` switch while `web` still serves the previous bundle (writes stay refused, because every loaded page
   sends the previous release and gets 409), then the wait for health to report the new release with a `current`
   schema;
10. the `web` switch, which reopens writes, then `smoke.sh`.

A failed check after the switch stops `backend` again.

**How `migrate` judges the pre-release backup.** `migrate --pre-release-backup NAME` verifies the backup with the
`backup-verify` rules before it connects, and accepts it only when it is fresh and belongs to this database:

- The dump must have **started at or after** the write-freeze time given by `--backup-not-before` (which `release.sh`
  passes: the freeze time when a migration is pending, otherwise its own start or the newest completed record), so
  that it holds every write `backend` committed.
- Without `--backup-not-before`, a 60-minute age limit applies, but only when no migration is pending; with a pending
  migration `migrate` refuses (`backup_freshness_unproven`).
- The backup's revision and database name must equal the database's.

A refusal (`backup_not_found`, `backup_invalid`, `backup_stale`, `backup_revision_mismatch`,
`backup_freshness_unproven`, `backup_database_mismatch`; exit 1) changes nothing and, while frozen, reopens writes on
the current release like any refusal. A backup directory the container cannot read is `backup_unreadable` (exit 2): the
container needs `--user "$(id -u):$(id -g)"`, which `release.sh` passes. Free text is refused: `--pre-release-backup`
names a backup directory.

**Grants inside `migrate`.** Every `migrate` applies the grants in the same transaction as the upgrade. A `refused`
result with a database-role code (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`,
`foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) rolls the whole run back and stops the
release before anything changes (exit 1, like any other refusal). Read the printed message, fix the cause
(`$PF --profile ops run --rm -T db-roles` for a role code, `DEPLOYMENT.md` §3.1) and run the release again. On a first
installation the order is: secret files, build, `db`, `db-roles`, `migrate`, first-run setup (`DEPLOYMENT.md` §3.1).

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | completed | observe (below) |
| 1 | stopped with nothing changed, or writes reopened on the current release | read the printed reason and `record.json`; fix the cause; run it again |
| 2 | could not run (usage, tools, environment) | nothing changed; fix the cause and run it again |
| 3 | `backend` left stopped | follow §6; read `regression.txt` or compare `pre-reconcile.json` and `post-reconcile.json` |
| 4 | the new release may be running and writable after a failed check, and the re-freeze failed | run `$PF stop backend` yourself, then follow §6 |
| 130, 143 | interrupted by Ctrl-C or TERM (record `outcome: interrupted`; the last step shows where); the interruption itself started or stopped no service | if it was interrupted during `migrate`, the outcome is unknown (record `migration.result: outcome_unknown`, `alembic.after: null`): run `$PF run --rm --no-deps -T backend python -m app.cli revision` before anything else; then `$PF ps` and §6 (after `switch_backend`, `.env.production` already names the new tag; `env-before.txt` in the record directory is the previous file) |

Two options handle findings of the pre-release reconciliation:

- `--accept-pre-release-findings` continues when the pre-release reconciliation has findings, and blocks only on
  findings that are absent from it (`deploy/production/reconcile_regression.py` compares the two reports).
- `--skip-pre-reconcile REASON` is for the rollback path 2 state, in which no image has the database revision as its
  head: the reason is recorded, and every post-release finding blocks.

#### The same release by hand

The manual equivalent, in the same order, with the current tag still in `.env.production`:

1. Record the current application and Alembic revisions
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) and run the pre-release reconciliation with the
   current tag (§7).
2. Build the candidate (never an existing tag):
   `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`. Then try
   the candidate image against the unchanged database:
   `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`. A failing check
   stops the release; nothing has changed.
3. When a migration is pending, freeze: `$PF stop backend`, confirm that `$PF ps --status running -q backend` prints
   nothing, then record the instant, `T=$(date -u +%Y-%m-%dT%H:%M:%SZ)`. Without a pending migration, record `T`
   before step 3a instead.
   3a. Take the backup from the candidate checkout:
   `deploy/production/backup.sh --kind pre-release --label <new> --tools-release <new> --operator "<name>"`, and note
   the NAME from the `BACKUP` line.
4. Migrate:
   `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate --pre-release-backup NAME --backup-not-before "$T"`
   (`--no-backup-reason TEXT` replaces the backup only for a first installation or a rehearsal). Without
   `--backup-not-before`, `migrate` refuses a pending migration (`backup_freshness_unproven`); without `--user` it
   cannot read the backup directory (`backup_unreadable`). Keep its JSON output (the `grants` field reports the
   applied grants, the `backup` field the verification) and the new revision.
5. Run the post-release reconciliation with the new tag (§7) and compare it with the pre-release report. Only findings
   absent from the pre-release report block step 7; findings that already existed stay open incidents for the owner
   to decide.
6. Write `PARTFLOW_RELEASE=<new>` into `.env.production` and clear `PARTFLOW_ACCEPT_SCHEMA_REVISION`, then run
   `$PF up -d --no-deps backend` and check health: `release` is the new tag and `schema` is `current`. On a database
   with no Administrator, complete first-run setup (the setup token is in the backend log) before opening access,
   with the backend started with one worker for it (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, then
   `$PF up -d backend`). Then enroll each Scan Station device (Administration → Scan Stations → `Devices…`).
7. Run `$PF up -d --no-deps web`; this reopens writes. Run `deploy/production/smoke.sh --release <new>` and the
   designated checks of authorization, scan focus and connectivity, and write and read-back.
8. Keep writes open only when every required check passes; otherwise run `$PF stop backend` again and follow §6. On
   exit 3 of `release.sh`, read `regression.txt` (or compare both reports) and then either reopen as in steps 6 and 7
   or follow §6.

### Observe

During the observation window you chose (record it with `--rollback-deadline` and `--observation-owner`), watch
errors, latency, locks, restarts, disk and what users report: the `check.sh` lines `errors`, `restarts` and `disk_*`,
slow reads (`"slow":true`) and `status` with its lock waits (§2).

- A check during the write freeze reports `backend` stopped (one notification), and skips the `status` checks while
  the release lock exists; once the lock is older than 4 hours it reports `FAIL database`.
- The release recreates `backend`, which deletes its log: run `check.sh --only errors --no-state` before the release
  (§2).
- Keep the previous release's images and the pre-release backup (which is never rotated) until the rollback deadline.
- Pages that were open during the switch show the update notice and must be reloaded
  ([`GUI_DESIGN.md`](../GUI_DESIGN.md) §3 rule 13); an unattended Scan Station or Production Board reloads itself once
  no dialog is open.

## 6. Rollback decision tree

Choose the first path whose condition holds. Recovery commands run with the release that matches the database.

1. **No schema migration happened:** redeploy the previous immutable release. Set the previous tag in
   `.env.production`, run `$PF up -d backend web`, then `deploy/production/smoke.sh --release <previous>`. This needs
   the previous release's images on the host: keep them through the rollback window and **never run
   `docker image prune -a`**; `release.sh` refuses to start when they are missing.
2. **The schema was migrated and is backward-compatible:** deploy the previous release only if the compatibility was
   explicitly verified and recorded. Read `database_revision` from
   `$PF run --rm --no-deps -T backend python -m app.cli revision` (run with the new release), set
   `PARTFLOW_RELEASE=<previous>` and `PARTFLOW_ACCEPT_SCHEMA_REVISION=<database_revision>` in `.env.production`, run
   `$PF up -d backend web`, then `deploy/production/smoke.sh --release <previous> --allow-accepted-schema`.
   - The override names exactly one revision that the previous release does not know. A revision it knows is ignored
     (`revision` shows `override_ignored`, and readiness stays `mismatch`). It never matches any other revision, and
     the next forward release clears it.
   - Before the override is set, the previous release refuses every change; station reads still record the device's
     last-seen time (device bookkeeping, no production data).
   - To release forward from this state, the pre-release reconciliation uses the candidate image (`release.sh` does
     this) or `--skip-pre-reconcile REASON`.
3. **The schema was migrated and is not backward-compatible, or the compatibility is unknown:** stop writes; restore
   the pre-migration database into a new, empty database in the production PostgreSQL instance
   (`createdb --template=template0`, restored in one transaction; the migrated database is kept untouched) and deploy
   the matching previous release, following the Path 3 procedure below. A restore of the pre-release backup never
   discards writes made after it without the path 4 escalation.
4. **New production writes happened after the migration:** do not restore blindly over them. Escalate; keep both the
   current database and the pre-release backup, decide on a forward fix or an audited data-recovery plan, and keep the
   application write-blocked.

**Never assume `alembic downgrade` is safe.** PartFlow deliberately protects append-only history, and a downgrade may
refuse or would discard newly supported data.

### Path 3 procedure

Path 3 applies only when the release record shows `writes_reopened_at: null` (the release stopped frozen) or the
owner records that no production write happened after the migration; otherwise it is path 4. Restoring into production
needs the **owner's approval, recorded in `rollback.json` before `backend` starts** on the restored database; without
it the procedure stops after `reconcile` with the application write-blocked.

The `.env.production` keys are read by Compose and are not exported to the shell, so every value below is set
explicitly. **Each block is a script: save it in the release record directory and run it with `sh -eu <file>`, never
paste it line by line.** A guard that does not hold prints why and stops the script before the next command, and so
does any failing command (`-e`) or unset variable (`-u`).

**Step A** runs in the **candidate** release's checkout: its `release.sh` took the backup, so its `backup-tools`
image verifies it. Step A checks that path 3 applies, confirms the write freeze and verifies the backup:

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

**Step B** runs in the **previous** release's checkout, with the previous release's images present, as three
scripts. **B1** checks the space and restores into a new database. The new name is never the live name, and
`createdb` refuses a name that already exists. The space rule is: free space on the database volume of at least the
database size plus twice the dump plus 1 GiB, for the restored copy, the write-ahead log (WAL) of its transaction and
a reserve:

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

Then edit `.env.production`: `PARTFLOW_RELEASE=<previous tag>`, `POSTGRES_DB=<NEWDB printed by B1>` and
`PARTFLOW_ACCEPT_SCHEMA_REVISION=` (empty). **B2** applies the grants and checks the restored database with the
previous release; the database roles already exist, because roles belong to the whole PostgreSQL cluster:

```sh
REC=<release record directory>
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current" (otherwise it exits non-zero: B2 stops)
rc=0
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json" || rc=$?
echo "reconcile exit $rc"
```

`reconcile` must exit 0, or exit 1 with only findings that are also in the release's `pre-reconcile.json`
(`deploy/production/reconcile_regression.py` compares them). Record the owner's approval in `$REC/rollback.json`
before B3. `rollback.json` carries the §1 fields plus `approved_by` (the owner), `approved_at` (UTC) and `reason`, the
new and the old database names, the backup name and both reconciliation results.

**B3** starts the previous release on the restored database and switches `web` only after `backend` reports itself
ready, so that writes reopen only on a verified backend. The health wait calls `127.0.0.1` on the published port
because `web` is published on the loopback address only:

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

**The migrated database keeps its name and content** for the path 4 analysis. The owner drops it after the
observation window, never a tool:
`$PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <old name>`.

### New-instance restore

Use this after a host failure (§8) or for a move (`SYNOLOGY_NAS.md` §10). Work on the new host, in the checkout of the
release that took the backup, with a **new, empty** `postgres_data` volume. First check the copied backup directory
(`sha256sum -c SHA256SUMS` and `backup-verify`, as in step A). Then, with the same `PF`, `BPATH` and space rule as step
B1 (with `DB_BYTES=0`):

1. Start the database: `$PF up -d db`.
2. Check that it is empty:
   `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '"'"'public'"'"'"'`
   must print `0`; otherwise stop, because the volume is not new.
3. Restore into the container's database with the step B1 `pg_restore` line:
   `$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' < "$BPATH/partflow.dump"`.
   A failure rolls back, the database stays empty, and the emptiness check passes again before a retry.
4. Provision the roles and apply the grants: `$PF --profile ops run --rm -T db-roles`, then
   `$PF --profile ops run --rm -T db-roles apply-grants`.
5. Run `revision` and `reconcile` as in step B2, and keep the report in the records directory.
6. Record the owner's approval in `<records-dir>/<UTC>-restore-<NAME>/restore.json` (the same fields as
   `rollback.json`). Then run `$PF up -d backend`, the step B3 health wait, `$PF up -d --no-deps web` and
   `deploy/production/smoke.sh --release <tag>`.

Record the data-loss window: writes made after the backup's `dump_started_at` are not in the restored database.

## 7. Reconciliation

Reconciliation is the read-only backend command `python -m app.cli reconcile`. It runs the checks below in one
read-only database snapshot and prints one JSON report on standard output; **it never repairs anything**.

### Running it

On the development stack:

```bash
# development stack
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

On the production stack, run it from the release checkout with the running tag. It also works while `backend` is
stopped (`--no-deps` starts no other service, but the database must be up):

```sh
f="reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
$PF run --rm --no-deps -T backend python -m app.cli reconcile > "$f"; rc=$?
```

Then apply the same report check (the `python3` line above). Before the switch of a release, the candidate image runs
check (j) with
`PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j` (§5, Execute: step 4
of `release.sh`, step 2 of the release by hand).

On a staging instance managed by the Deployment Admin tool `pf`: `pf` refuses `exec` and `run`, so run the raw Compose
form of `SYNOLOGY_ADMIN.md` §14 outside the tool, never at the same time as `pf update`, `pf backup`, `pf reset-db`,
`pf purge` or `pf restore-instance`. Write the report to the operator's home directory, never into a directory that
`pf` manages:

```sh
f="$HOME/partflow-reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo \
    PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<db>' \
    DEPLOY_ADMIN_INSTANCE_ID=<instance UUID from 'pf instances'> \
    docker compose \
    --project-directory /volume1/docker/partflow/repo \
    --env-file /volume1/docker/partflow/config/.env \
    -p partflow-staging \
    -f /volume1/docker/partflow/control/compose.nas.yaml \
    exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
    || echo "could not run: no complete report (exit $rc)"
```

The paths, project name and instance identifier are the instance's own (the example values of `SYNOLOGY_ADMIN.md`
§14 are shown). All three variables are required: Compose refuses to start without them and writes no report. A
wrong `DEPLOY_ADMIN_INSTANCE_ID` mislabels every resource Compose creates (`SYNOLOGY_ADMIN.md` §14); `exec` itself
creates no container, volume or network.

### Options, exit codes and the report rule

- `--check ID` (repeatable, `a` to `j`; the other checks are reported as `skipped`), `--statement-timeout SECONDS`
  (1 to 3600, default 300), `--max-findings N` (1 to 10000, default 100 findings listed per check; `finding_count`
  always holds the full count).
- Exit codes: `0` clean, `1` mismatch (at least one finding), `2` could not run or incomplete (an `error` check or a
  run-level error wins over findings).
- **The exit status counts only when the report holds one complete JSON document whose `exit_code` equals it.** An
  empty or unparseable report means "could not run", whatever the status. (Compose itself exits 1 before anything
  runs when the service is stopped or the project is wrong.)
- Keep the report in the deployment record (§1, "Smoke and reconciliation results").

### Checks

Each check keeps the original requirement as its definition:

| Check | Requirement |
| --- | --- |
| (a) | current-position projections replay from non-reversed Movement history; |
| (b) | every active or closed flow has a valid conservation history, plus the cross-row invariants of [`SLICE1_DATA_MODEL.md`](../SLICE1_DATA_MODEL.md) §17 except the audit-row invariant (enforced by the transaction protocol and covered by the API tests); |
| (c) | per-PN introduced quantity reconciles with active, stocked, scrapped and reversed outcomes under the canonical rules; |
| (d) | Machine assigned quantities reconcile with the flows currently on each Machine; |
| (e) | demand `released_quantity` derives from `RECEIVED` evidence; |
| (f) | demand `allocated_quantity` and Work Order `completed_at` reconcile with active allocation rows; an authorized beyond-demand correction (allocation rows recorded `exceeds_demand`) is not reported as allocation beyond the requested quantity; |
| (g) | no retained Movement references a purged row; |
| (h) | no append-only table was changed outside an approved archival or purge path, checked as guard integrity: the guard triggers are present, enabled and unchanged, the guard functions' source is unchanged, `partflow_app` and `partflow_maintenance` hold exactly their grants (no UPDATE, DELETE or TRUNCATE on append-only history), the roles keep their safe attributes and no membership, PUBLIC and default privileges hold nothing, and `session_replication_role` is not set to `replica`. It cannot see a change made by a superuser who disabled a trigger or set `session_replication_role` in their own session and restored it afterwards (an accepted limit); |
| (i) | Hot list entries are active demand (the query of `DEPLOYMENT.md` §5); |
| (j) | canonical identity under the running interpreter and database: canonical PNs, case-insensitive Worker badges, the Asset Tag prefix rule, every canonical-form CHECK constraint re-evaluated under the running database collation and character classification, the collation version, and duplicate probes of the identity keys that do not rely on indexes (the platform-upgrade identity check). |

Check (g) reports `not_applicable` until Movement-history archival exists, and check (h) reports `not_applicable`
only on a database without the PartFlow database roles (development, test, staging); both are neutral for the exit
code. In the production stack (`DATABASE_ROLES_REQUIRED=true`) a missing role is a finding.

### Repairing check (h) findings

Reconciliation never repairs. The commands below are the operator's, and the owner decides:

- privilege codes on `partflow_app`, `partflow_maintenance` or PUBLIC, and schema, database and default-privilege
  codes for those grantees: `$PF --profile ops run --rm -T db-roles apply-grants`;
- role codes except `ROLE_OWNS_OBJECTS` (`ROLE_ATTRIBUTE`, `ROLE_MEMBERSHIP`, `ROLE_MISSING`) and a role-scoped
  `REPLICATION_ROLE_SETTING` (`<db>/<role>` or `*/<role>`): `$PF --profile ops run --rm -T db-roles`, then
  `$PF --profile ops run --rm -T db-roles apply-grants`;
- a finding for another database role (any privilege, column, schema, database or default-privilege code —
  `PRIVILEGE_EXCESS`, `COLUMN_PRIVILEGE`, `SCHEMA_PRIVILEGE`, `DATABASE_PRIVILEGE`, `DEFAULT_PRIVILEGE` — whose
  grantee is a role other than PUBLIC, `partflow_app` and `partflow_maintenance`; an `apply-grants` or `migrate`
  refusal `foreign_grantor`): find out who granted it and why, and revoke it as the owner or the grantor;
  `apply-grants` deliberately leaves it in place;
- `TABLE_UNCLASSIFIED` and `TABLE_MISSING` (and the `apply-grants` or `migrate` refusals `table_unclassified` and
  `table_missing`): a table created or dropped outside a migration is an incident (§8) and the owner decides.
  `apply-grants` and `migrate` refuse until the stray table is removed (or a release classifies it) or the missing
  table is restored;
- trigger codes (`TRIGGER_MISSING`, `TRIGGER_CHANGED`, `TRIGGER_DISABLED`, `TRIGGER_ENABLE_MODE`), function codes
  (`GUARD_FUNCTION_MISSING`, `GUARD_FUNCTION_CHANGED`), `ROLE_OWNS_OBJECTS` (and the `role_owns_objects` refusal), a
  database-wide `REPLICATION_ROLE_SETTING` (`<db>/*`) and `REPLICATION_ROLE_ACTIVE`: an incident (§8), and the owner
  decides.

**Never repair by running migrations again or by downgrading them.** A run that raced a grant or trigger change can
show a finding that disappears: run it again before acting.

### Operating rules

- One read-only snapshot. The command takes only `ACCESS SHARE` table locks, before the snapshot, and no row or
  advisory locks. It fails after waiting 5 s for a table lock (sooner when `--statement-timeout` is shorter), or at
  once when its lock request deadlocks with another session. **Never run it at the same time as a migration**, and run
  it off-peak.
- Platform-upgrade rehearsal for check (j): for a Python or Unicode Character Database (UCD) upgrade, run
  `--check j` from the candidate backend image against the current database. For a glibc or PostgreSQL-image change,
  run the restore drill onto the candidate server,
  `deploy/production/restore-test.sh --backup <latest> --db-image <candidate image>` (§4; its full `reconcile` runs
  check (j) and the check (h) trigger definitions on that server), or, after an in-place image change of the same
  major version, `reconcile --check j` before writes reopen. A `COLLATION_VERSION_MISMATCH` is an owner decision. The
  owner decides on re-canonicalization before any upgrade.
- For a Work Order reported `not_completed_but_fully_allocated`, `expected` is the replayed value, not a repair
  proposal; the owner picks the completion date in the incident.
- A non-zero exit is an incident (§8). **Never edit history or projections directly**; the owner decides each repair.
  A mismatch never triggers an automatic repair.

### Scheduled reconciliation

`deploy/production/scheduled-reconcile.sh` is the daily form, recommended at 04:00 from the running release's
checkout, as the account that owns `PARTFLOW_BACKUP_DIR`.

- It runs the full `reconcile` in the production stack and writes the report to
  `~/partflow-monitoring/reconcile/<UTC timestamp>-reconcile.json` (`--reports-dir`; directory mode 0700, report mode
  0600).
- It applies the exit-code rule above through `monitor_report.py`: it prints
  `RECONCILE clean|mismatch|error|could_not_run <report>` and one `FAIL <check> <title>` line per failed check, and
  exits 0 (clean), 1 (mismatch) or 2 (could not run, including a report that is not complete).
- It refuses to run while a release holds its lock or the backup lock (exit 2), stops a run longer than
  `--max-runtime-minutes` (default 60), and never runs at the same time as a migration.
- `last-result.txt` in the reports directory holds the last run's lines. Every non-zero exit reaches the scheduler's
  failure notification, which names only check ids, titles and counts.

**Report handling:** a report has mode 0600 and may contain Worker badge values (check (j)). Keep reports like
backups, and never attach them to tickets or emails. No tool deletes them; prune them by hand (§9).

## 8. Incident response

### Suspected duplicate, lost or uncertain write

- Stop the affected workflow if quantity integrity may be at risk.
- Keep the request time, station, User or Worker, PN, flow and `device_event_id`.
- Before any retry, correlate the backend log by `request_id`, `device_event_id`, PN, QuantityFlow, Scan Station and
  Worker (the commands are in §2), and look at the server's result and history.
- Retry only with the original idempotency key, and only when appropriate.
- **Never edit Movement history directly.**
- Use the canonical Undo or correction workflow only after the committed state is known.

### Guard-integrity finding (reconcile check (h))

- Freeze writes (`$PF stop backend`, §5) when a trigger or guard function changed, a trigger is disabled, or
  `session_replication_role` is set database-wide or is active: the ordinary guards then do not fire for any session,
  `partflow_app` included.
- For the setting, the owner runs `ALTER DATABASE <db> RESET session_replication_role` inside `db`.
- Compare the triggers and functions with the migration source.
- The owner decides the repair and whether history must be checked against the last backup.
- Run `reconcile` and the privilege probe (`DEPLOYMENT.md` §3.1) before reopening writes.

### Database or storage pressure

- Block new writes before the disk is full (the write freeze, §5).
- Keep the logs and metrics.
- The storage alert is a `disk_*` line of `check.sh` below 15 % free: `disk_data` measures the database volume,
  `disk_backup` the backup directory, `disk_docker` the Docker root (images and container logs) and `disk_archive`
  the archive directory.
- **Do not delete PostgreSQL files, volumes, Movement rows or backups ad hoc.**
- Know the space rules: a backup needs twice the newest dump plus a reserve free in the backup directory; a path 3
  restore needs the database size plus twice the dump plus 1 GiB free on the database volume (§6); and a stale backup
  lock (`backup_lock_stale`) blocks backups and releases until it is removed (§3).
- Expand the storage. The archive and purge maintenance of Movement history is planned but not implemented yet
  ([`../IMPLEMENTATION_ROADMAP.md`](../IMPLEMENTATION_ROADMAP.md), Phase 16).
- Run the reconciliation before reopening writes.

### Host failure

- Prevent two live copies: confirm that the failed instance cannot accept writes.
- Prepare the recovery host.
- Restore the latest verified backup with the matching release, using the new-instance restore (§6).
- Run the reconciliation and the smoke tests.
- Record the data-loss window: the writes made after the backup's `dump_started_at`.
- Redirect the clients only after approval: the owner's approval is recorded in `restore.json` before `backend`
  starts.

## 9. Routine schedule

The schedules below are recommended after go-live (`DEPLOYMENT.md` §5); none of them blocks use.

| Frequency | Tasks |
| --- | --- |
| Continuous | `check.sh` every 15 minutes: health, restarts, disk, certificate, backup age and backend errors, with the thresholds of §2 (backup older than 26 h, disk free below 15 %, certificate expiring within 21 days, any restart-count increase, any backend error record). The notification is the scheduler's failure notification; an unchanged failure is repeated every 6 h. |
| Daily | The scheduled `backup.sh --kind daily --keep-daily 14 --keep-weekly 8` (with the owner's values) at 02:00, as in the platform guides; review the scheduler's and the platform tool's notifications (exit 4 includes a daily backup that fails verification) and the off-host copy; review critical errors; `scheduled-reconcile.sh` at 04:00, after the backup; review `last-check.txt`, `<reports-dir>/last-result.txt` and the notifications. |
| Weekly | Review the capacity trend and database growth (`growth.tsv` and `docker stats --no-stream`); review failed sign-ins and authorization events with `$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'` (`refusal.type` and `refusal.code` tell apart a sign-in refused or locked, a permission denied, `station_device_required` or `station_device_mismatch`, and `csrf_rejected`; the `app.application.authentication` sign-in lines name a user id only); review pending security updates of the host. |
| Monthly | Prune old reconciliation reports by hand after review (no tool deletes them; they may hold badge values); apply updates, on a test system first where possible; review users and roles, firewall rules, secrets and the contacts in this runbook; review the database roles with `reconcile --check h`; remove pre-release and manual backups whose observation window has ended, and daily backups that the rotation reported invalid, after review (never an archive). |
| On a role-password rotation | First run `check.sh --url https://<partflow-host> --only errors --no-state` (recreating `backend` deletes its log, §2). Then a short write freeze: replace the role's password file, `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, `$PF up -d --force-recreate --no-deps backend`, then check health. A plain `up -d backend` does not pick up the new password (the container is not recreated), and running `db-roles` while the backend serves makes its new connections fail. For the owner password, run `ALTER ROLE … PASSWORD` inside `db` first, then replace `postgres_password` (no service restart is needed: only the one-shot `migrate` and `db-roles` use it). |
| Quarterly, or after a material schema change | `restore-test.sh --backup <latest daily>` (§4); compare its `restore_to_ready` and `total` timings with how long the factory can work without PartFlow (the recovery time objective, RTO), and review the reconciliation results. |
| Before a PostgreSQL-image or host glibc change | `restore-test.sh --backup <latest> --db-image <candidate>` (§7). |
| Before every release | `release.sh` takes the verified pre-release backup (§5); review the migrations, the rollback decision and the smoke-test plan. |

The owner chooses the retention values, the schedules and the acceptable recovery point and recovery time. The values
in this runbook are working examples, not service-level commitments.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | `b405c6e` | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-09 | 4271f61 | Added Work Order Demand log searches and the error check to run before recreating `backend`. |
| 2026-10-09 | 03bd90c | Added monitoring: structured logs with request ids, the `status` command, `check.sh` and the scheduled reconciliation. |
| 2026-10-08 | 92727f6 | Made the rollback path 3 scripts stop at every failed guard and reopen `web` only after `backend` is ready. |
| 2026-10-08 | a330383 | Added production backups, the isolated restore drill, the pre-release backup inside the write freeze and rollback path 3. |
| 2026-10-08 | 1116b07 | Added the database roles, grants applied by every migration and the repairs for reconcile check (h). |
| 2026-10-08 | b820e7a | Added the release flow with `release.sh`, the write freeze and rollback paths 1 and 2. |
| 2026-10-08 | d7e86a9 | Pointed the procedures at the production Compose stack. |
| 2026-10-07 | 3fb66df | Added the read-only reconciliation command and its checks. |
| 2026-09-02 | d2f68de | First version, with the Vietnamese mirror. |
