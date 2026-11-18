# PartFlow Operations Runbook

> **Language:** English is the source of truth. [Tiếng Việt](./OPERATIONS_RUNBOOK.vi.md).

This runbook holds the procedures for running PartFlow production once it is installed: checking that it is healthy,
taking and testing backups, installing a new release, rolling back, running the reconciliation, handling problems,
the routine schedule and the archival of Movement history. It is written for the operator, who today is also the owner. The installation itself is in
[`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) (or [`VPS.md`](./VPS.md)), and every setting and limit of the stack is
described in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1.

## Find your task

| I want to … | Section |
| --- | --- |
| Check that PartFlow is healthy, or find the cause of an error | §2 |
| Take a backup by hand, verify one, understand the backup folder or the hourly backup task | §3 |
| Prove that a backup can be restored | §4 |
| Install a new release | §5 |
| Undo a release | §6 |
| Restore after a host failure, or move PartFlow to another host | §6 (New-instance restore, Moving to another host) |
| Run or read the reconciliation | §7 |
| Solve a problem: common symptoms and incidents | §8 |
| Know what to do daily, weekly and monthly; change a password; remove old backups | §9 |
| Reset a forgotten password or login name, rename a user, or remove a user's access | §8 (Sign-in and user accounts) |
| Find what a past release did | §1 |
| Use the short `pf` command for an installation, an update, a backup, a restore or the status | Before you run any command |
| Delete Work Orders and Part Numbers that were entered only for testing | §10 |
| Understand the history archive job, its pre-purge backup and its purge, check or confirm an archival run, recover from a failed run, check an archive, or return disk space after a purge | §11 |
| Handle a reconciliation finding after a purge of Movement history (`archive.sh` exit 4) | §8 (Reconcile finding after a purge) |

## Before you run any command

1. Open a terminal on the host as the operating account. On the Synology NAS: `ssh` with your DSM administrator, then
   `sudo -i` ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §3).
2. Go to the root folder of the release checkout and set the `PF` shorthand. A new terminal session forgets `PF`, so
   set it again every time:

   ```sh
   cd /volume1/docker/partflow/repo
   PF="docker compose -f compose.production.yaml --env-file .env.production"
   ```

   These are the paths of the NAS installation. On a VPS, the checkout and the configuration file are elsewhere
   ([`VPS.md`](./VPS.md) §3 and §6): wherever this runbook names `.env.production`, use the VPS file instead.
3. Use the checkout of the **running** release for daily work. During a release, use the checkout of the **new**
   release. In the NAS installation both are the same folder `repo/`; `git checkout <tag>` switches it.

**The short command `pf`.** `deploy/production/pf` runs the documented procedures in one command each, in their
order, and stops at the first step that fails; it adds no procedure of its own. `deploy/production/pf --help` prints
its options.

| Command | What it runs |
| --- | --- |
| `pf install` | The first installation of the checked-out release on an empty database: [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §7 and §8. |
| `pf update <tag>` | `git fetch --tags`, `git checkout <tag>`, then `release.sh` (§5); the approver defaults to the operator. |
| `pf backup [--reason TEXT]` | A manual backup (§3). |
| `pf restore <NAME> --approved-by <owner>` | The new-instance restore of §6 into an empty database, with the owner's approval recorded in `restore.json` before `backend` starts. |
| `pf status [--url https://<partflow-host>]` | The containers, `/api/health` and the `status` summary (§2); with `--url`, `check.sh --no-state`. |
| `pf delete-permanently --work-order <number> --part-number <PN> --reason TEXT [--confirm]` | The preview of deleting Work Orders and Part Numbers entered only for testing; with `--confirm`, a verified manual backup and then the deletion in one transaction (§10). |
| `pf archive` | The hourly work of the history archive job now, `archive.sh` without `--scheduled` (§11): it exports, verifies and copies a requested run, takes its pre-purge backup and **purges** the archived Movement history from the database, then runs the reconciliation. |

Conventions: a one-shot job that reads or writes the backup folder runs with `--user "$(id -u):$(id -g)"`, because
that folder has mode 0700. `<partflow-host>` is the host name people type in the browser, `<backup-dir>` is the value
of `PARTFLOW_BACKUP_DIR`, `<archive-dir>` the value of `PARTFLOW_ARCHIVE_DIR`, `<secrets-dir>` the value of
`PARTFLOW_SECRETS_DIR`, `<records-dir>` the records directory
`~/partflow-deployments`, and `<NAME>` the name of a backup. Times in reports and records are Coordinated Universal Time (UTC), and reports and records are JSON
documents.

## 1. Deployment records

Every release, restore drill, rollback and restore leaves a folder in `<records-dir>` (mode 0700). Keep these folders:
they tell you which backup belongs to which release and what was checked.

| Folder | Written by | Main files |
| --- | --- | --- |
| `<UTC>-<tag>/` | `release.sh` | `record.json`; `preflight.log`; `pre-reconcile.json` and `post-reconcile.json`, the reconciliation before and after the change; `migrate.json` and `migrate.log`; `env-before.txt`, the `.env.production` from before the switch; `regression.txt`, findings that are new after the change; `smoke.txt`; and one log per step |
| `<UTC>-restore-test-<NAME>/` | `restore-test.sh` | `evidence.json`, the result of the drill (§4) |
| the folder of the release being undone | you, during rollback path 3 (§6) | `rollback.json`, `rollback-reconcile.json` |
| `<UTC>-restore-<NAME>/` | you, during a new-instance restore (§6) | `restore.json` |
| `archive/<UTC>/` | `archive.sh`, only for an hourly work that did something or failed (§11) | one JSON report and one log per step (`recover`; `next`, then `next-2`, `next-3` and so on; `export`, `verify`, `copy`, `purge`, `status`); `backup.out` and `backup.log` of the pre-purge backup; `reconcile.json` and `reconcile.log`, the reconciliation after a purge; and `messages.log` |
| `archive/hourly-slot` | `archive.sh --scheduled` (§11) | the hourly stamp: the minute of the last hourly work that was attempted |
| `archive/reconcile-after-purge` | `archive.sh` (§11) | the incident record of a reconciliation after a purge that did not end clean: the run, the result, the failing checks and the path of the report. While it exists, `archive.sh` works on no run; you remove it when the incident is closed (§8, Reconcile finding after a purge) |

`record.json` holds the release (tag, commit, previous tag, image IDs), the database revisions before and after, the
operator and the approver, the times, the pre-release backup and its verification, the migration, smoke and
reconciliation results, and how the run ended: `outcome`, `freeze_completed_at` (when `backend` was confirmed stopped,
or `null` without a freeze), `writes_reopened_at` and `refrozen`.

For a manual operation, for example a rollback by path 1, save its outputs in the folder of the release it concerns,
with a short note of what was done, by whom, when and why.

## 2. Health checks and diagnosis

### Quick checks

```sh
$PF ps
$PF logs --since=15m backend web db
curl -fsS https://<partflow-host>/api/health
curl -fsS https://<partflow-host>/api/health/live
$PF run --rm --no-deps -T backend python -m app.cli revision
```

- `$PF ps` lists `db`, `backend` and `web` as running and `(healthy)`.
- `/api/health` is **readiness**: it reports `release`, `commit` and `schema` (`current`, `accepted`, `mismatch` or
  `unknown`) and the revisions. `"status":"ok"` with `"schema":"current"` means all is well. A 503 with
  `"schema":"mismatch"` means that the database revision does not match the running release (an interrupted release,
  or a wrong tag in `.env.production`: §5 and §6). A 503 with `"schema":"unknown"` means that the database cannot be
  reached (`$PF ps db`, `$PF logs db`).
- `/api/health/live` is **liveness** only. The container health checks use it, so a container stays healthy while
  readiness answers 503.
- `revision` prints the same facts from a one-off container, and also works while `backend` is stopped.
- A 502 or 504 JSON answer comes from `web`: `backend` is stopped or does not answer (`$PF ps backend`,
  `$PF logs --tail=100 backend`).

### The full host check

`deploy/production/check.sh` runs every check of the scheduled task. By hand, **always add `--no-state`**: without
it, a manual run moves the error cursor and the restart baseline, and the next scheduled run would miss what the
manual run saw. Add the same `--cacert` or `--resolve-to` options as the scheduled task.

```sh
deploy/production/check.sh --url https://<partflow-host> --no-state
```

It prints one line per check, `PASS|FAIL|SKIP <id> <reason>`, in this order, then optional `NOTE` lines:

| Check | Fails when |
| --- | --- |
| `https` | `/api/health` does not answer 200 with schema `current` and the release named in `.env.production` |
| `certificate` | the certificate expires within 21 days (`--min-cert-days`) |
| `containers` | there is not exactly one running, healthy `db`, `backend` and `web` |
| `restarts` | a restart count increased since the previous full run |
| `errors` | `backend` wrote an `ERROR` or `CRITICAL` record since the previous full run (up to three request ids are shown) |
| `disk_data` | the database volume has less than 15 % free (`--min-free-percent`) |
| `disk_backup` | the backup folder has less than 15 % free |
| `disk_docker` | the Docker storage (images and container logs) has less than 15 % free |
| `disk_archive` | the archive folder (`PARTFLOW_ARCHIVE_DIR`) has less than 15 % free; `SKIP` while archival is not set up (the value is empty) |
| `database` | the `status` report could not run, did not answer within 120 s, or reported a database error |
| `schema` | `status` reports the schema as not ready |
| `backup_age` | no published backup is found, the backup folder cannot be read, or the newest backup of any kind (a pre-purge backup included) completed longer ago than the scheduled backup interval of Administration → Backups plus 2 hours (3 hours initially; `--max-backup-age-hours` sets another limit) |
| `archival_proposal` | the read-only `history-archive status` reports a finding, named in the line: `copy_confirmation_overdue`, `last_run_failed`, `archive_dir_unusable`, `copy_dir_unusable` or `receipt_not_copied` (§11); or the reconciliation after a purge has not ended clean, named first as `reconcile_after_purge` (§8, Reconcile finding after a purge); `SKIP` while archival is not set up |

- The lines `database`, `schema` and `backup_age` come from one read-only `status` run, and `archival_proposal` from
  one read-only `history-archive status` run in the `maintenance` service (§11) and from the incident record
  `<records-dir>/archive/reconcile-after-purge` of `archive.sh`, which `check.sh` only reads. While a release holds its
  lock, or `release.sh` holds the backup lock, these four lines are `SKIP`. When such a lock is older than 4 hours, the
  run reports `FAIL database` and names the lock, because a killed `release.sh` leaves it behind (§3, Backup lock and
  release lock).
- Exit codes: 0 nothing to report, 1 at least one `FAIL`, 2 the check could not run; both 1 and 2 reach the
  scheduler's failure notification. An unchanged set of failures is reported again only after 6 hours
  (`--renotify-hours`); the output then says `already reported`.
- `check.sh` keeps its state in `~/partflow-monitoring` (mode 0700), including `last-check.txt` (the lines of the last
  run), `growth.tsv` (the database size once a day) and `archive-status.json` (the last `history-archive status`
  report).
- The `errors` check reads the log of the current `backend` container only, and Docker deletes a container's log with
  the container. After `backend` was recreated (by a release or a password change), the run adds a `NOTE errors`
  line. **Before you recreate `backend` by hand**, run
  `deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` and keep what it reports.

### The status report

```sh
$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status
docker stats --no-stream
```

`status` prints one JSON document about the database size, connections, lock waits, Movement rows and size, schema
readiness and backup age. Its `result` is `ok`, `attention` or `error`, with exit code 0, 1 (a finding such as
`backup_stale` or `schema_not_ready`) or 2 (`configuration_invalid`, `database_unavailable`, `lock_timeout`,
`statement_timeout`, `database_error`, `internal_error`). The lock waits (`database.locks`) are reported, never judged.
`status` runs one read-only transaction as `partflow_app` with a 5 s lock timeout; keep its `--statement-timeout`
(default 20 s) below 30 s, so that it never makes a release's `migrate` wait. `docker stats` shows the processor and
memory use of each container.

### Logs and request ids

The backend writes one JSON object per line. Every answer, refusals and failures included, carries an HTTP request id
in the header `X-Request-ID`, and every backend record of that request carries it as `request_id`. To find a request:

```bash
$PF logs --no-log-prefix backend | grep '"request_id":"<id>"'
```

A request id changes whenever a request is sent again, so a production command is followed and retried by its
`device_event_id` (shown as the "request identity" on screen), never by its request id. To follow a command, a Part
Number (PN), a QuantityFlow, an Area, a Work Order Demand or a Scan Station:

```bash
$PF logs --no-log-prefix backend | grep '"device_event_id":"<id>"'
$PF logs --no-log-prefix backend | grep '"part_number":"<PN>"'
$PF logs --no-log-prefix backend | grep -E '"quantity_flow_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"(area|source_area|target_area|starting_area)_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"work_order_demand_id":<n>[,}]|"work_order_demand_ids":\[([0-9]+,)*<n>[],]'
$PF logs --no-log-prefix backend | grep '"station_id":"<id>"'
```

A number is matched with the `,` or `}` that follows it, so that `1` does not also match `10`. An Area is also found
as the source, target or starting Area of a command, and a Work Order Demand also inside the list of an allocation.
What each record contains, and what is never logged, is in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 (Logs).

A 502 or 504 JSON answer from `web` (`server_unavailable`) means that the outcome of a change is unknown. Find it by
its `request_id` in the `web` log, and settle the change by its original `device_event_id`. **Never retry a
production command that timed out with a new `device_event_id`** before its result is known: retry only with the
original one, so that an uncertain answer can never record a change twice.

**Logs are kept only for a few days.** Each container keeps at most 5 log files of 10 MB. With 5,000 commands a day,
that is about 9 days of backend log and 18 days of `web` log. The successful automatic refreshes of the Production
Board are not logged, but every open Area Board or Tracking screen adds `web` records every 15 seconds, so with
several of them open the `web` log covers fewer days. **Copy what an investigation needs as soon as it starts.**

### Database permission errors

The backend connects as the database role `partflow_app`. A `permission denied` error (SQLSTATE 42501) in its log
means that a code path tried to change protected history or lacks a grant. It is an incident (§8), never a reason to
grant more. Run `reconcile --check h` (§7) before anything else.

## 3. Backups

### What a backup is

`deploy/production/backup.sh` takes a logical backup of the PartFlow database while `db` runs. It runs from the root
of a release checkout: the running release's for scheduled, manual and pre-purge backups, the new release's when
`release.sh` calls it. One backup is one folder `<backup-dir>/<NAME>/`:

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-scheduled,
                                         20261008T140000Z-pre-release-v1.0.0-rc.2
                                         and 20271110T050102Z-pre-purge-run-3-20271110T042000Z
```

- `kind` is `scheduled` (the backup task that runs every hour), `manual` (taken by hand, with a reason),
  `pre-release` (taken by `release.sh`, with the target release tag as `label`) or `pre-purge` (taken by `archive.sh`
  right before the purge of an archival run, with the run's archive name `run-<id>-<UTC time>` as `label`, §11). `daily`
  is the scheduled kind of earlier releases: such backups are still verified, restored and rotated like scheduled
  ones. Every kind is verified and restored the same way.
- The folder holds exactly four files: `partflow.dump` (the database, dumped inside `db` with
  `pg_dump --format=custom --no-owner --no-privileges`), `partflow.dump.list` (its table of contents), `manifest.json`
  (when, where and from which release and database revision the backup was taken, the PostgreSQL version, the
  checksums, the operator and the reason) and `SHA256SUMS`.
- It is assembled in `<backup-dir>/.partial/` and published by one rename, so a backup either exists completely or not
  at all. Other entries of the folder, such as DSM's `@eaDir`, are ignored.
- A backup contains every table, including password hashes and session and device token digests. **Treat the backup
  folder and every copy of it as secret.** It contains no grants: after a restore, they are derived again from the
  code (§4).
- The backup folder holds production backups only, never another installation's or a drill's; its rules are in
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 (Configuration file, `PARTFLOW_BACKUP_DIR`).
- A database in an unexpected state (an unknown revision or unknown tables) is still backed up, with a warning and
  `tables_checked: false` in the manifest, because a faithful backup is worth more than none. `migrate` refuses such a
  backup, and the restore drill fails at its revision step.

### Taking a backup by hand

```sh
deploy/production/backup.sh --kind manual --operator "<your name>" --reason "<why>"
```

On success, the standard output is exactly one line, `BACKUP <NAME> <absolute path>`; a pre-purge backup adds a third
value, the time its dump started (`BACKUP <NAME> <absolute path> <YYYY-MM-DDTHH:MM:SSZ>`, read from its verification
report), which the purge records. Progress lines such as `backup: dump ok (1234 ms)` go to standard error. The backup
task runs `deploy/production/backup.sh --kind scheduled --operator scheduler` every hour
([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.2); Scheduled backups, below, explains it. `release.sh` takes the
pre-release backup itself; its manual form, for a release by hand, is
`deploy/production/backup.sh --kind pre-release --label <target tag> --tools-release <target tag> --operator "<your name>"`.
`archive.sh` takes the pre-purge backup itself (§11, The pre-purge backup).

| Option | Meaning |
| --- | --- |
| `--kind scheduled\|manual\|pre-release\|pre-purge`, `--operator NAME` | Required. `--kind daily`, `--keep-daily` and `--keep-weekly` of earlier releases are refused with the command to use instead. |
| `--reason TEXT` | Required for `manual`. |
| `--label TAG` | The target release tag for `pre-release`; the archive name of the run, `run-<id>-<UTC time>`, for `pre-purge`. Required for these two kinds, refused otherwise. |
| `--tools-release TAG` | The release whose `backup-tools` image writes and verifies the manifest (default: `PARTFLOW_RELEASE`). |
| `--no-rotate` | `scheduled` only: keep every scheduled and pre-purge backup this run. |
| `--reserve-mib N` | Free space kept beyond twice the newest dump (default 1024). |
| `--lock-wait-minutes N` | While another backup holds the backup lock, wait up to N minutes for it, checking every 15 seconds (0 to 1440; default 0, refused at once). A stale lock is refused at once. `archive.sh` passes its own `--backup-wait-minutes` here (60 by default). |

Text values are 1 to 500 characters without control characters, `"` or `\`. Before writing, `backup.sh` requires free
space of twice the newest dump of any kind (or the database size when there is no dump yet) plus the reserve.

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Completed and verified, or (`scheduled`) no backup was due: the standard output is then `NOT_DUE <NAME>`, the newest scheduled backup. | Nothing. |
| 1 | Refused before writing: `backup_running` (also when it is still running after `--lock-wait-minutes`), `backup_lock_stale`, `insufficient_space` or `name_exists`. | Read the reason; nothing was written. |
| 2 | Could not run: options, tools, configuration, `db` not running, the backup lock cannot be created, or (`scheduled`) the backup policy cannot be read from the database. | Fix the cause and run it again. |
| 3 | Failed: no backup was published, the published backup failed verification, or (`pre-purge`) its verification report names no dump start. | Never use a backup that the message names; remove it after review. |
| 4 | The backup is complete and verified, but the rotation failed or found a backup that fails verification. | Read the rotation report on standard error (Retention, below). |

### Verifying a backup

Verify any backup, read-only, with the same checks that `migrate` and the restore drill use. The second command
checks the files independently of any image; run it after every copy.

```sh
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"
(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)
```

`backup-verify` prints one JSON document and exits with 0 (`verified`), 1 (`invalid`: **do not use the backup**) or 2
(could not check). Put `PARTFLOW_RELEASE=<tag>` in front of the first command to use another release's
`backup-tools` image.

### Scheduled backups

The backup task runs `backup.sh --kind scheduled` every hour ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.2,
[`VPS.md`](./VPS.md) §8). **Administration → Backups** in PartFlow sets how often it takes a backup and how many
scheduled and pre-purge backups it keeps; a change applies from the task's next run. Each run:

1. takes the backup lock, then reads the backup policy from the database (`python -m app.cli backup-policy` in a
   one-off `backend` container, as the application role); when the policy cannot be read, it exits with 2;
2. asks `backup-due` whether a backup is due: when the newest scheduled backup started less than the interval ago
   (less ten minutes, so that an hourly task never skips an hour), it writes nothing, prints `NOT_DUE <NAME>` and
   exits with 0;
3. otherwise takes and verifies the backup, then applies the retention (below).

| Administration → Backups | Initial value | Allowed |
| --- | --- | --- |
| Scheduled backup | Every hour | every 1, 2, 3, 4, 6, 8, 12 or 24 hours |
| Hourly backups kept | 48 | 0 to 720 |
| Daily backups kept | 30 | 1 to 3650 |
| Weekly backups kept | 52 | 0 to 520 |
| Pre-purge backups kept | 3 | 1 to 100 |

Changing these needs the permission Configure system settings, and every change is audited. When the policy cannot
be read, or lacks one of these values, the run exits with 2 and writes nothing.

### Retention, the off-host copy and alerts

A scheduled backup ends with the rotation of the scheduled and the pre-purge backups, with the values of
Administration → Backups. To preview it without deleting anything (here with the initial values):

```sh
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-hourly 48 --keep-daily 30 --keep-weekly 52 --keep-pre-purge 3 --dry-run
```

- It keeps the newest verified scheduled backup of each of the newest `--keep-hourly` hours, `--keep-daily` days and
  `--keep-weekly` calendar weeks that hold one (hours, days and ISO weeks in UTC; the periods overlap), so the newest
  scheduled backup is always kept. The daily backups of earlier releases count as scheduled backups.
- Of the verified pre-purge backups it keeps the newest `--keep-pre-purge` and deletes the older ones; the two
  retentions never count each other's backups. A pre-purge backup is never rotated by its own run. With an image from
  before the purge, whose backup policy has no pre-purge value, the rotation leaves every pre-purge backup alone.
- It verifies every candidate first. A backup that fails verification is not counted and **never deleted**:
  `backup.sh` exits with 4 and you review it (§9).
- **Pre-release and manual backups are never rotated**: you remove them after review (§9). No tool ever rotates or
  deletes an archive folder.
- Leftovers in `.partial/` older than 24 hours are removed.
- With the initial values the backup folder holds at most 130 scheduled backups (48 + 30 + 52, fewer while the
  periods overlap) and 3 pre-purge backups. Plan its space for that many dumps, plus the pre-release and manual
  backups.

The platform's backup tool encrypts the backup folder and copies it off the host, never a hand-made copy: Hyper Backup
with client-side encryption on the NAS ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.3), restic to object storage on a
VPS ([`VPS.md`](./VPS.md) §8).

Alerts, once the schedules are set up: a failed scheduled backup (any non-zero exit) raises the scheduler's failure
notification; a failed off-host copy raises the backup tool's own notification; and the `check.sh` check
`backup_age` fails when the newest backup is older than the interval of Administration → Backups plus 2 hours (§2).
With a backup every hour, at most one hour of work can be lost (the recovery point objective, RPO); after the loss of
the host, everything since its last off-host copy. Until the backup task is scheduled, the only backups are the ones
`release.sh` takes before each release and any manual backup.

### Backup lock and release lock

Backups never run at the same time. The folder `<backup-dir>/.backup.lock` is the lock; its file `owner` holds
`host`, `pid`, `started_at`, `by` (`backup.sh` or `release.sh`) and the backup `name` or the `release`. A second run is
refused, never queued: `backup_running` means that a backup, or a release holding the lock, is in progress, so wait.
The one exception is the pre-purge backup of `archive.sh`, which waits for the lock (`--lock-wait-minutes`, §11); no
release can hold the lock then, because `release.sh` refuses to start while `archive.sh` works. A scheduled backup that
starts while the pre-purge backup runs is refused as during a release (one failure email; the next run backs up as
usual).
`release.sh` holds the lock from the write freeze (or from its backup) until the new `backend` runs. **Start a release
right after a scheduled backup:** a running backup stops a release before the freeze with nothing changed (start it
again once the backup has finished), and a scheduled backup that starts during a release is refused (exit 1, with its
failure email; the release takes its own backup, and the next run backs up as usual). A lock that cannot be created at
all (a full, read-only or inode-exhausted backup folder) is reported as could-not-run (exit 2), never as
`backup_running`.

`backup_lock_stale` means that the lock was left by a run that no longer exists: it was killed, ran out of memory, or
the power failed. **It is never removed automatically.** Remove it by hand after these checks:

```sh
cat "<backup-dir>/.backup.lock/owner"   # host, pid, started_at, by and name|release
ps -p <pid>                             # on that host: must report no such process
$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"'   # must print 0
rm -r "<backup-dir>/.backup.lock" "<backup-dir>/.partial/<name>"   # the name from the owner file, when it has one
```

**Never remove a lock whose owner is `release.sh` while that release is running.** The release lock is a separate,
empty folder, `<records-dir>/.release.lock`, which a killed `release.sh` leaves behind. While it exists, `check.sh`
skips its `status` checks, and after 4 hours it reports `FAIL database`. When no `release.sh` is running (the first
command prints nothing), remove it:

```sh
ps -ef | grep '[r]elease\.sh'
rmdir ~/partflow-deployments/.release.lock
```

## 4. Restore drill

A backup is only trusted once it has been restored. The restore drill restores a backup into a separate, temporary
stack, checks it, and removes the stack; it never touches the production database. Run it once after go-live, every
quarter, after a large schema change, and before a change of the PostgreSQL image or of the host's C library (glibc).

### Running the drill

Run it from the root of a release checkout:

```sh
deploy/production/restore-test.sh --backup <NAME> --operator "<your name>"
# a candidate PostgreSQL image, and a list of findings that are already known:
deploy/production/restore-test.sh --backup <NAME> --operator "<your name>" \
    --db-image postgres:16.14-bookworm --baseline-report <records-dir>/<release folder>/pre-reconcile.json
```

`deploy/production/restore-test.sh --help` lists every option: `--release TAG` (the release to run on the restored
database; by default the release in the manifest, and a backup taken between a migration and the switch needs it
named), `--tools-release TAG`, `--db-image IMAGE`, `--baseline-report FILE`, `--project`, `--http-port`,
`--edge-subnet`, `--records-dir`, `--space-factor` and `--keep`.

### What the drill does

The drill runs in its own Compose project `partflow-restore-<suffix>` (never `partflow-production`), with its own
volume and networks, its own loopback port and edge subnet, the fixed database name `partflow_restore_test`,
throwaway database-role passwords (deleted at the end), one backend worker and the backup folder mounted
**read-only**. It never runs `migrate`, never writes to the backup folder, and refuses a project that already has
containers or volumes. It needs free space of five times the dump plus 1 GiB on the Docker storage
(`--space-factor`).

It follows the general restore procedure (below) and records each step in the evidence file: `verify` (the backup),
`db_start` (an empty database on the PostgreSQL image of `compose.production.yaml`, or on `--db-image`), `restore`
(in one transaction), `roles` and `grants`, `revision`, `app_start` (`backend` and `web` ready), `reconcile` (the full
reconciliation, which replays Movement history against every stored total and allocation), `smoke`, `evidence`, and
`teardown` (`down -v` of the drill project only, after the evidence exists; `--keep` keeps the project and prints the
command).

### Result

- **`passed`:** the reconciliation exits with 0, and both the guard check (h) and the identity check "Canonical
  identity under the running interpreter and database" (check (j)) pass. The grants are fresh in a drill, so any
  finding of check (h) is a real defect. With `--db-image`, check (j) tests the candidate PostgreSQL image.
- **`passed_with_preexisting_findings`:** with `--baseline-report`, every finding is already in that report, and
  check (h) passes.
- **`failed`:** anything else.

The evidence file `<records-dir>/<UTC>-restore-test-<NAME>/evidence.json` records the outcome, the failed step, the
backup and drill facts, the server version and collation, the PostgreSQL major versions of the backup and the drill
with `server_major_match` (`false` is also printed as a warning), the duration of each step, and the reconciliation
results. `restore_to_ready` and `total` tell you how long a real restore would take: compare them with how long the
factory can work without PartFlow (the recovery time objective, RTO).

| Exit | Meaning |
| --- | --- |
| 0 | `passed` or `passed_with_preexisting_findings` |
| 1 | `failed`, or not enough free space |
| 2 | Could not run, or interrupted; nothing it created is left running unless `--keep` was given |
| 3 | The teardown failed: the evidence stands, and the message prints `docker compose -p <project> down -v` |

Reading individual screens of the restored data with a signed-in account is not automated (`read_model_readback:
not_automated` in the evidence). To check them by hand, run the drill with `--keep`, sign in at the printed loopback
port from the host, and remove the project afterwards with the printed command.

### The general restore procedure

Every restore follows these rules, whether the drill does it or you do it by hand (§6). **Restore into a separate,
empty database, never over the only production database.**

1. Verify the dump and manifest checksums.
2. Use the same PostgreSQL major version, or a documented compatible one.
3. Create an empty database.
4. Restore with `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`: one transaction, so a
   failed restore leaves the database empty.
5. Create the database roles (`provision-roles`) and apply the grants (`apply-grants`) before the application
   starts: a backup contains no grants, and `partflow_app` cannot work without them.
6. Start the matching release and check the Alembic revision.
7. Run the health checks, representative screens, the reconciliation and the smoke checks. **The guard check (h) must
   pass before a restored database serves production.**
8. Record how long the restore took and the result.
9. Remove the separate copy only after the evidence is kept.

Never put the production database name into a drill command.

## 5. Installing a new release

On the NAS, the short form is in [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §12. This section explains what happens and
what to do when `release.sh` stops.

### Before maintenance

- Choose the exact release tag and read what it changes, especially its database migrations: how long they lock
  tables, how long they run and how much disk they need on data of realistic size.
- Confirm that continuous integration (CI) passed for that commit.
- Check that the newest backup is recent (§3) and that the last off-host copy succeeded. `release.sh` takes its own
  verified pre-release backup in any case: **inside the write freeze** when a migration is pending, so that nothing
  can be written between the backup and the migration, otherwise just before the switch.
- Confirm that the images of the running release are still on the host: rollback path 1 needs them (§6), and
  `release.sh` refuses to start without them.
- Choose a time when no Work Order import runs, and start right after a scheduled backup has finished (the backup
  task starts at every full hour on the NAS).
- Tell the users when the change starts and when you will decide on a rollback, and ask the stations to finish their
  open dialogs.
- `release.sh` runs the reconciliation on the running release before it changes anything, and treats its findings as
  in §7. Every recovery command before the switch uses the **running** tag: the new tag lives only in the shell until
  the switch.

### Write freeze

The write freeze is **stopping `backend`** (`$PF stop backend`); PartFlow has no other maintenance mode.

- `backend` finishes the requests in flight, including an import, and the stop waits up to 200 s. With several
  workers, requests sent meanwhile hang and end as a 504 or 502 ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Backend
  processes). **Start a freeze when no import is running.**
- While `backend` is stopped, `web` still serves the screens and answers every `/api` request with its JSON 504, then
  502. Every screen shows the OFFLINE banner within about a second, and every change is blocked.
- Open dialogs keep their drafts. A change that raced the stop has an unknown outcome: it keeps its `device_event_id`
  and is retried with it after writes reopen on the **same** release. After a reopen on a **new** release, the old
  page cannot send it (it is refused with 409): the user reloads the page and checks the Area or Work Order before
  repeating the action.
- An import that was cut off leaves only whole, committed Work Orders.
- `reconcile`, `revision`, `migrate` and the backup run in one-off containers against `db` during the freeze.
- **Reopen** with `$PF up -d backend` on the same release, only after the required checks passed. At a release switch,
  the reopen is the switch of `web`.

### Execute

Run `release.sh` from the root of the new release's checkout, with its tag checked out (on the NAS:
`git fetch --tags` and `git checkout <new tag>` in `repo/`):

```bash
deploy/production/release.sh --release <new tag> --operator "<your name>" --approver "<your name>"
```

The tag has the form `vMAJOR.MINOR.PATCH`, optionally followed by `-alpha.N`, `-beta.N` or `-rc.N`
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §10.2). One person may give the same name as operator and approver.
`deploy/production/release.sh --help` lists every option. The backup options are:

- **Neither option (normal):** `release.sh` takes the verified pre-release backup itself.
- **`--pre-release-backup <NAME>`:** use an existing `backup.sh` backup instead. Allowed only for a release
  **without** a pending migration; with one, `release.sh` stops with nothing changed, because the backup must then be
  taken inside the freeze.
- **`--no-backup-reason "<text>"`:** no pre-release backup, for a rehearsal only; the reason is recorded.

The other options set the configuration file, the records directory, optional record fields such as
`--rollback-deadline` and `--observation-owner`, and a rehearsal on a throwaway Compose project.

`release.sh` runs and records these steps:

1. **Preflight:** the tools, the configuration file, the tag form, and build inputs that match the tag's commit (no
   changed or untracked files). It refuses, changing nothing, when a secret file is missing, empty or not a regular
   file, when a Compose project `partflow-staging` exists on the same Docker daemon, when the running release's images
   are missing, and when another release holds the lock.
2. The current database revision, and the reconciliation with the running release (`pre-reconcile.json`).
3. The build of the new images. An existing tag is never rebuilt; it is reused only when both images were built from
   this commit as this release.
4. The identity check (j) and `revision` of the new image against the unchanged database; they must report this
   release and commit. When `revision` reports `blocked_by_data`, the release stops here and changes nothing:
   `blocking_data` in `candidate-revision.json` lists the stored records the new release cannot accept yet (for example
   two Areas with one name in a Department). Fix them in PartFlow, then run the release again.
5. The write freeze, when a migration is pending.
6. The verified pre-release backup (`backup.sh --kind pre-release`), inside the freeze when a migration is pending.
   The backup lock is held from here until step 9.
7. `migrate`, which verifies the backup first and applies the migrations and the grants.
8. The reconciliation after the migration (`post-reconcile.json`).
9. The switch of `backend` to the new release, while `web` still serves the old screens: their writes are refused
   (409), because every open page sends the old release. Then the wait until health reports the new release with
   schema `current`.
10. The switch of `web`, which reopens writes, then `smoke.sh`.

A failed check after the switch stops `backend` again.

**How `migrate` judges the pre-release backup.** It verifies the backup before it connects, and accepts it only when
it is fresh and belongs to this database:

- The dump started at or after the time given by `--backup-not-before`, which `release.sh` passes (the freeze time
  when a migration is pending), so that it holds every committed write.
- Without `--backup-not-before`, a 60-minute age limit applies, but only when no migration is pending; with a pending
  migration, `migrate` refuses (`backup_freshness_unproven`).
- Its revision and database name equal the database's.

A refusal (`backup_not_found`, `backup_invalid`, `backup_stale`, `backup_revision_mismatch`,
`backup_freshness_unproven`, `backup_database_mismatch`; exit 1) changes nothing and, during a freeze, reopens writes
on the current release. `backup_unreadable` (exit 2) means that the container cannot read the backup folder: it needs
`--user "$(id -u):$(id -g)"`, which `release.sh` passes.

**Grants inside `migrate`.** Every `migrate` applies the grants in the same transaction as the migration. A database-
role refusal (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`, `foreign_grantor`,
`table_unclassified`, `table_missing`, `not_superuser`) rolls the whole run back and stops the release before
anything changes. Read the message, fix the cause (`$PF --profile ops run --rm -T db-roles` for a role problem; §7,
Repairing check (h) findings, for the others) and run the release again.

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Completed. | Observe (below). |
| 1 | Stopped with nothing changed, or writes reopened on the current release. | Read the message and `record.json`, fix the cause, run it again. |
| 2 | Could not run (options, tools, configuration). | Nothing changed; fix the cause and run it again. |
| 3 | `backend` left stopped: writes stay frozen and nothing reopens by itself. | Read the failed step's output and `regression.txt` (or compare `pre-reconcile.json` with `post-reconcile.json`). Then choose one: **reopen**, only when every required check passes: before the switch, `$PF up -d backend` (the release named in `.env.production`); after the switch (`.env.production` already names the new tag), `$PF up -d --no-deps backend`, then `$PF up -d --no-deps web`, then `deploy/production/smoke.sh --release <tag>`. Or **roll back** (§6). |
| 4 | The new release may be running and accepting writes after a failed check, and stopping it failed. | Run `$PF stop backend` yourself at once, then follow §6. |
| 130, 143 | Interrupted by Ctrl-C or a TERM signal (`outcome: interrupted`; the last step shows where). The interruption itself started or stopped nothing. | If it happened during `migrate`, the outcome is unknown (`migration.result: outcome_unknown`): run `$PF run --rm --no-deps -T backend python -m app.cli revision` before anything else, then `$PF ps`, then §6. After the `backend` switch, `.env.production` already names the new tag; `env-before.txt` in the record folder is the previous file. |

Two options handle findings of the reconciliation before the change:

- `--accept-pre-release-findings` continues although the reconciliation before the change has findings, and blocks
  only on findings that are new after it (`deploy/production/reconcile_regression.py` compares the two reports).
- `--skip-pre-reconcile REASON` is for the state after rollback path 2, in which no image has the database revision
  as its head: the reason is recorded, and every finding after the change blocks.

#### The same release by hand

The manual equivalent of `release.sh`, in the same order, with the current tag still in `.env.production`:

1. Record the current application and database revisions
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) and run the reconciliation with the current tag
   (§7).
2. Build the new images (never an existing tag):
   `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`. Then
   test the new image against the unchanged database:
   `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`. A failing check
   stops the release; nothing has changed.
3. When a migration is pending, freeze: `$PF stop backend`, check that `$PF ps --status running -q backend` prints
   nothing, then record the instant, `T=$(date -u +%Y-%m-%dT%H:%M:%SZ)`. Without a pending migration, record `T`
   just before step 3a.
   3a. Take the backup from the new release's checkout:
   `deploy/production/backup.sh --kind pre-release --label <new> --tools-release <new> --operator "<your name>"`, and
   note the NAME from the `BACKUP` line.
4. Migrate:
   `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate --pre-release-backup NAME --backup-not-before "$T"`.
   Without `--backup-not-before`, `migrate` refuses a pending migration (`backup_freshness_unproven`); without
   `--user`, it cannot read the backup folder (`backup_unreadable`). Keep its JSON output (the `grants` field reports
   the grants, the `backup` field the verification) and the new revision.
5. Run the reconciliation with the new tag (§7) and compare it with the one of step 1. Only new findings block step
   7; findings that already existed stay open incidents for the owner to decide.
6. Write `PARTFLOW_RELEASE=<new>` into `.env.production` and empty `PARTFLOW_ACCEPT_SCHEMA_REVISION`, then run
   `$PF up -d --no-deps backend` and check health: `release` is the new tag and `schema` is `current`.
7. Run `$PF up -d --no-deps web`, which reopens writes. Run `deploy/production/smoke.sh --release <new>` and the
   checks of sign-in, permissions, scanning and a change that is saved and read back.
8. Keep writes open only when every required check passes; otherwise run `$PF stop backend` again and follow §6.

### Observe

Right after `release.sh` ends with 0, check by hand with test data what `smoke.sh` cannot: sign in, check that
permissions behave as before, scan at a Scan Station (the scan field keeps the focus), and save a change and read it
back. Then, during the observation time you chose (record it with `--rollback-deadline` and `--observation-owner`),
watch errors, response times, lock waits, restarts, disk and what users report: the `check.sh` lines `errors`,
`restarts` and `disk_*`, slow reads (`"slow":true`) and `status` with its lock waits (§2).

- A check during the write freeze reports `backend` stopped (one notification) and skips the `status` checks while
  the release lock exists; after 4 hours it reports `FAIL database`.
- The release recreates `backend`, which deletes its log: run `check.sh --only errors --no-state` before the release
  (§2).
- Keep the previous release's images and the pre-release backup (which is never rotated) until the rollback deadline.
- Pages that were open during the switch show the update notice and reload themselves
  ([`../GUI_DESIGN.md`](../GUI_DESIGN.md) §3 rule 13): a Scan Station or Production Board after a 10-second countdown
  in the notice, every other page once nobody has used it for 1 minute and nothing on it is unsaved (the notice
  counts down the last 10 seconds of that minute), and never while a dialog is open. Nobody needs to visit each
  computer; whoever is working on a page can click `Reload page` sooner.

## 6. Rolling back

Choose the **first** path whose condition holds. Recovery commands always run with the release that matches the
database. **Never assume that `alembic downgrade` is safe:** PartFlow protects append-only history, and a downgrade
may refuse or would discard newly supported data. Every path recreates `backend`, which deletes its log: keep its
recent errors first with `deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` (§2).

1. **Path 1: the release did not change the database.** Check it: this command exits with 0 and shows
   `"state": "current"` when the previous release matches the database.

   ```sh
   PARTFLOW_RELEASE=<previous tag> $PF run --rm --no-deps -T backend python -m app.cli revision
   ```

   Then go back to the previous release, which needs its images on the host:

   ```sh
   git checkout <previous tag>
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<previous tag>|' .env.production
   $PF up -d backend web
   deploy/production/smoke.sh --release <previous tag>
   ```

   If `up` fails with `No such image`, the previous images were deleted and path 1 is impossible; never run
   `docker image prune -a` during the rollback window.

2. **Path 2: the database was migrated, and the previous release is verified to work with the new schema.** Use it
   only when that compatibility was explicitly checked and recorded. Read `database_revision` from the first command
   below (it prints it even when it exits with 1), then switch to the previous release with an override for exactly
   that revision:

   ```sh
   $PF run --rm --no-deps -T backend python -m app.cli revision
   git checkout <previous tag>
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<previous tag>|' .env.production
   sed -i 's|^PARTFLOW_ACCEPT_SCHEMA_REVISION=.*|PARTFLOW_ACCEPT_SCHEMA_REVISION=<database_revision>|' .env.production
   $PF up -d backend web
   deploy/production/smoke.sh --release <previous tag> --allow-accepted-schema
   ```

   - The override names exactly one revision that the previous release does not know. A revision it knows is ignored
     (`revision` shows `override_ignored`, and readiness stays `mismatch`). It never matches any other revision, and
     the next release clears it.
   - Before the override is set, the previous release refuses every change; station reads still record the device's
     last-seen time, which holds no production data.
   - While the override is in place, add `--allow-accepted-schema` to the scheduled `check.sh` command; otherwise its
     `https` check fails.
   - To release forward from this state, `release.sh` runs the reconciliation before the change with the new image,
     or you give `--skip-pre-reconcile REASON`.

3. **Path 3: the database was migrated, and the previous release is not compatible, or its compatibility is
   unknown.** Stop writes, restore the pre-release backup into a new, empty database in the same PostgreSQL server
   (the migrated database stays untouched), and start the previous release on it, following the Path 3 procedure
   below. Path 3 never discards writes made after the backup; when there are any, it is path 4.
4. **Path 4: production writes happened after the migration.** Do not restore over them. Keep both the current
   database and the pre-release backup, keep PartFlow write-blocked, and let the owner decide on a forward fix or an
   audited data-recovery plan.

**Restores and the history archive job.** Before you restore a backup into production (path 3, the new-instance
restore below, or §8 Reconcile finding after a purge), disable the history archive task in the host scheduler (on the
NAS: untick **Enabled** in Task Scheduler), and enable it again only after you have checked the runs in
Administration → History archival & purge. A restored database holds every archival run in the state it had when the
backup's dump started:

- A run found `RUNNING` is failed by the next start of the job, and nothing is purged again. That is why a purge is
  undone by restoring that run's pre-purge backup (§8, Reconcile finding after a purge), never a later backup.
- A run found `PURGING` resumes its purge at the next start, unless the task is disabled or the incident record
  `<records-dir>/archive/reconcile-after-purge` exists (§8). A backup holds a `PURGING` run when it was taken during a
  purge (the hourly backup runs while a long purge works) or while a stopped purge waited to be resumed.

### Path 3 procedure

Path 3 applies only when the release record shows `writes_reopened_at: null` (the release stopped frozen) or the owner
records that no production write happened after the migration; otherwise it is path 4. Restoring into production
needs the **owner's approval, recorded in `rollback.json` before `backend` starts** on the restored database; without
it the procedure stops after `reconcile`, with PartFlow write-blocked. Disable the history archive task before step A
(Restores and the history archive job, above).

The `.env.production` values are read by Compose and are not available in the shell, so every value below is set
explicitly. **Each block is a script: save it in the release record folder and run it with `sh -eu <file>`, never
paste it line by line.** A guard that does not hold prints why and stops the script before the next command, and so
does any failing command (`-e`) or unset variable (`-u`).

**Step A** runs in the checkout of the **new** release, whose `release.sh` took the backup, so that its
`backup-tools` image verifies it. In the NAS installation this is `repo/` with the new tag still checked out. Step A
checks that path 3 applies, confirms the write freeze and verifies the backup:

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

**Step B** runs in the checkout of the **previous** release, with the previous release's images on the host: in the
NAS installation, run `git checkout <previous tag>` in `repo/` first. It has three scripts. **B1** checks the space
and restores into a new database. The new name is never the live name, and `createdb` refuses a name that already
exists. The database volume needs free space of at least the database size plus twice the dump plus 1 GiB, for the
restored copy, the write-ahead log (WAL) of its transaction and a reserve:

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
previous release; the database roles already exist, because roles belong to the whole PostgreSQL server:

```sh
REC=<release record directory>
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current" (otherwise it exits non-zero: B2 stops)
rc=0
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json" || rc=$?
echo "reconcile exit $rc"
```

`reconcile` must exit with 0, or with 1 and only findings that are also in the release's `pre-reconcile.json`
(`deploy/production/reconcile_regression.py` compares them). Record the owner's approval in `$REC/rollback.json` before
B3, as a JSON document with the host, the previous and the candidate release tags, the operator, the times (UTC),
`approved_by` (the owner), `approved_at` (UTC), `reason`, the new and the old database names, the backup name and
both reconciliation results.

**B3** starts the previous release on the restored database and switches `web` only after `backend` reports itself
ready, so that writes reopen only on a verified backend. The health wait calls `127.0.0.1` on the published port,
because `web` listens on the loopback address only:

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
observation time, never a tool: `$PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <old name>`.

### New-instance restore

Use this after a host failure (§8) or for a move to another host (below). It installs the release on a host whose
database volume is **new and empty**, then restores a backup into it.

1. Prepare the host with its platform guide, at the **same release** as the backup (`release.tag` in its
   `manifest.json`), up to and including the image build: on the NAS, [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §1 to §7
   step 1. Do not create the roles or run `migrate` (§7 step 3 there): this restore replaces them.
2. Copy the backup folder into the host's backup folder and check it, as in step A:
   `(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)` and
   `$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"`.
3. Check the space with the rule of step B1 (with the database size as 0): free space of at least twice the dump plus
   1 GiB on the database volume.
4. Start the database: `$PF up -d db`.
5. Check that it is empty; this command must print `0`, otherwise stop, because the volume is not new:
   `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '"'"'public'"'"'"'`
6. Restore, with `BPATH` set to the path of the backup folder (`BPATH=<backup-dir>/<NAME>`):
   `$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' < "$BPATH/partflow.dump"`.
   A failure rolls back and the database stays empty; the check of step 5 passes again before a retry.
7. Create the roles and apply the grants: `$PF --profile ops run --rm -T db-roles`, then
   `$PF --profile ops run --rm -T db-roles apply-grants`.
8. Run `revision` and `reconcile` as in step B2, and keep the report in the records directory.
9. Record the owner's approval in `<records-dir>/<UTC>-restore-<NAME>/restore.json` (the same fields as
   `rollback.json`). Then start the application and check it. With the approval recorded, `backend` and `web` start
   together:

   ```sh
   $PF up -d backend web
   $PF ps
   curl -fsS http://127.0.0.1:<PARTFLOW_HTTP_PORT>/api/health
   deploy/production/smoke.sh --release <tag>
   ```

   Run `$PF ps` again until `backend` and `web` are `(healthy)`; `/api/health` must show the restored release and
   `"schema":"current"` before you run `smoke.sh`.
10. Continue with the platform guide's HTTPS step (NAS: §9). Skip first-run setup: the restored database already has
    its Administrators. Scan Station devices keep working when the host name stays the same; with a new host name,
    enroll them again.
11. Create (or enable) the history archive task (NAS: [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.8) only after you have
    checked the runs in Administration → History archival & purge: a run that the backup holds in `PURGING` resumes
    its purge at the task's first start (Restores and the history archive job, above).

Record the data-loss window: writes made after the backup's `dump_started_at` are not in the restored database.

### Moving to another host

A move installs the same release on the new host and restores a final backup taken inside a write freeze on the old
host. The two installations must never accept writes at the same time.

1. Prepare the new host as in step 1 of the new-instance restore, at the release that runs on the old host.
2. Rehearse: copy a recent backup to the new host's backup folder and run the restore drill there (§4).
3. Announce the move, then start the write freeze on the old host: `$PF stop backend`.
4. On the old host, take the final backup inside the freeze:
   `deploy/production/backup.sh --kind manual --operator "<your name>" --reason "move to new host"`.
5. Copy that backup folder to the new host's backup folder over an encrypted connection (for example `scp -r`).
6. Restore it with the new-instance restore above (steps 2 to 9), with the owner's approval recorded.
7. Set up HTTPS on the new host for the same host name, then point the host name to the new host: lower the DNS
   time to live (TTL) beforehand, change the record, and test from the clients.
8. Keep the old host's `backend` stopped and its data untouched until you are sure you will not go back. Never start
   it again with writes while the new host runs.
9. Move the archive folder too (§11, Moving to a new host): the archives belong to the installation, and nothing else
   holds them. The off-host copy folder, when one is set, stays where it is; set `PARTFLOW_ARCHIVE_COPY_DIR` on the new
   host only when that folder can be mounted there.

## 7. Reconciliation

Reconciliation is the read-only command `python -m app.cli reconcile`. It runs the checks below in one read-only
database snapshot and prints one JSON report on standard output; **it never repairs anything**.

### Running it

Run it from the release checkout with the running tag. It also works while `backend` is stopped (`--no-deps` starts
nothing else, but `db` must run):

```sh
f="reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
$PF run --rm --no-deps -T backend python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
```

The last command prints `clean 0`, `mismatch 1` or `error 2`, or "could not run" when the report is not complete.
To test a new image before a release: `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`.
The development form is in the root [`README.md`](../../README.md).

### Options, exit codes and the report rule

- `--check ID` (repeatable, `a` to `j`; the other checks are reported as `skipped`), `--statement-timeout SECONDS`
  (1 to 3600, default 300) and `--max-findings N` (1 to 10000, default 100 findings listed per check;
  `finding_count` always holds the full count).
- Exit codes: `0` clean, `1` mismatch (at least one finding), `2` could not run or incomplete.
- **The exit code counts only when the report is one complete JSON document whose `exit_code` equals it.** An empty
  or unreadable report means "could not run", whatever the exit code.
- Keep the report in the deployment record (§1).

### Checks

| Check | What it verifies |
| --- | --- |
| (a) Current positions replay from Movement history | The current positions equal a replay of the Movement history that was not reversed; a flow whose history an archival run purged takes its expected status from its carry-forward record (§11). |
| (b) Quantity Flow history and conservation | Every active or closed flow has a valid conservation history, plus the cross-row rules of [`SLICE1_DATA_MODEL.md`](../SLICE1_DATA_MODEL.md) §17 except the audit-row rule; a flow whose history was purged is checked through its carry-forward record. |
| (c) Per-PN quantity balance | The quantity introduced per PN equals its active, stocked, scrapped and reversed outcomes, the carry-forward records of purged flows included. |
| (d) Machine assigned quantities | The quantity assigned to each Machine equals the flows on it. |
| (e) Release evidence and Work Order status | Each demand's `released_quantity` follows from its `RECEIVED` evidence and from the carry-forward records of purged flows. |
| (f) Allocations and Work Order completion | Each demand's `allocated_quantity` and each Work Order's `completed_at` follow from the active allocations, and no PN is allocated beyond its stocked quantity, the stocked quantity of purged flows included; an authorized allocation beyond the demand (`exceeds_demand`) is not a finding. |
| (g) Retained Movements reference no purged row | The purge of Movement-history archival left whole flow components: no flow with a purged record still has a Movement, no lineage edge joins a purged flow and a retained one, every purged record belongs to a run whose purge started, every record of a finished purge was purged or skipped, and each run's purged counts equal its purged records. |
| (h) Append-only guards and database-role privileges intact | The guard triggers and functions are present, enabled and unchanged, the row guard `trg_part_movements_guard_delete` of the purge included; `partflow_app` and `partflow_maintenance` hold exactly their grants (`partflow_app` has no `UPDATE`, `DELETE` or `TRUNCATE` on append-only history; `partflow_maintenance` has `DELETE` on `part_movements` only, which the row guard limits to the archived rows of a run in `PURGING`), keep their safe attributes and have no membership; `PUBLIC` and the default privileges hold nothing; and `session_replication_role` is not `replica`. It cannot see a change by a superuser who disabled a trigger and restored it afterwards (an accepted limit). |
| (i) Hot list entries are active demand | The Hot list query of [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5 returns no row. |
| (j) Canonical identity under the running interpreter and database | Canonical PNs, case-insensitive Worker badges, the Asset Tag prefix rule and every canonical-form constraint hold under the running database's sorting rules, the collation version is unchanged, and no identity key is duplicated. |

Check (h) reports `not_applicable` only on a database without the PartFlow roles (development, tests, staging); this
is neutral for the exit code. In production, a missing role is a finding. Right after every purge, `archive.sh` runs
the full reconciliation itself (§11); a finding then is an incident (§8, Reconcile finding after a purge).

### Repairing check (h) findings

The reconciliation never repairs. These commands are the operator's, and the owner decides:

- A privilege, schema, database or default-privilege code on `partflow_app`, `partflow_maintenance` or `PUBLIC`:
  `$PF --profile ops run --rm -T db-roles apply-grants`.
- A role code other than `ROLE_OWNS_OBJECTS` (`ROLE_ATTRIBUTE`, `ROLE_MEMBERSHIP`, `ROLE_MISSING`), or a
  `REPLICATION_ROLE_SETTING` scoped to one role (`<db>/<role>` or `*/<role>`):
  `$PF --profile ops run --rm -T db-roles`, then `$PF --profile ops run --rm -T db-roles apply-grants`.
- A finding for another database role (`PRIVILEGE_EXCESS`, `COLUMN_PRIVILEGE`, `SCHEMA_PRIVILEGE`,
  `DATABASE_PRIVILEGE` or `DEFAULT_PRIVILEGE` for a grantee other than `PUBLIC`, `partflow_app` and
  `partflow_maintenance`, or the refusal `foreign_grantor`): find out who granted it and why, and revoke it as the
  owner or the grantor. `apply-grants` deliberately leaves it in place.
- `TABLE_UNCLASSIFIED` or `TABLE_MISSING` (and the refusals `table_unclassified` and `table_missing`): a table created
  or dropped outside a migration is an incident (§8), and the owner decides. `apply-grants` and `migrate` refuse until
  the stray table is removed (or a release classifies it) or the missing table is restored.
- A trigger code (`TRIGGER_MISSING`, `TRIGGER_CHANGED`, `TRIGGER_DISABLED`, `TRIGGER_ENABLE_MODE`), a function code
  (`GUARD_FUNCTION_MISSING`, `GUARD_FUNCTION_CHANGED`), `ROLE_OWNS_OBJECTS` (and the refusal `role_owns_objects`), a
  database-wide `REPLICATION_ROLE_SETTING` (`<db>/*`) or `REPLICATION_ROLE_ACTIVE`: an incident (§8), and the owner
  decides.

**Never repair by running migrations again or by downgrading them.** A run that raced a change of a grant or a
trigger can show a finding that then disappears: run it again before acting.

### Operating rules

- One read-only snapshot: the command takes only shared table locks and gives up after waiting 5 s for one (sooner
  with a shorter `--statement-timeout`). **Never run it at the same time as a migration**, and run it outside busy
  hours.
- Before an upgrade of Python or of its Unicode data, run `--check j` from the new backend image against the current
  database. Before a change of glibc or of the PostgreSQL image, run the restore drill onto the candidate server
  (`deploy/production/restore-test.sh --backup <latest> --db-image <candidate image>`, §4), or, after an in-place image
  change of the same major version, `reconcile --check j` before writes reopen. A `COLLATION_VERSION_MISMATCH` is an
  owner decision, and the owner decides on re-canonicalization before any upgrade.
- For a Work Order reported as `not_completed_but_fully_allocated`, `expected` is the replayed value, not a repair
  proposal; the owner chooses the completion date in the incident.
- A non-zero exit is an incident (§8). **Never edit history or stored totals directly**; the owner decides each
  repair, and a finding never triggers an automatic repair.

### Scheduled reconciliation

`deploy/production/scheduled-reconcile.sh` is the daily form, recommended at 04:00, from the running release's
checkout, as the operating account ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.5).

- It runs the full reconciliation and writes the report to
  `~/partflow-monitoring/reconcile/<UTC timestamp>-reconcile.json` (`--reports-dir`; folder mode 0700, report mode
  0600).
- It prints `RECONCILE clean|mismatch|error|could_not_run <report>` and one `FAIL <check> <title>` line per failed
  check, and exits with 0 (clean), 1 (mismatch) or 2 (could not run, including an incomplete report).
- It refuses to run while a release holds its lock or the backup lock (exit 2), stops a run longer than
  `--max-runtime-minutes` (default 60), and never runs at the same time as a migration.
- `last-result.txt` in the reports folder holds the lines of the last run. Every non-zero exit reaches the scheduler's
  failure notification, which names only check ids, titles and counts.

A report may contain Worker badge values (check (j)): keep reports like backups, and never attach them to tickets or
emails. No tool deletes them; remove them by hand after review (§9).

## 8. Incidents and troubleshooting

### Common problems

| Symptom | Cause and what to do |
| --- | --- |
| Every screen shows OFFLINE | `backend` is stopped or not healthy: `$PF ps`, `$PF logs --tail=100 backend`. If `release.sh` stopped writes (exit 3), follow §5 (exit code table) and §6. |
| `/api/health` answers 503 with `"schema":"mismatch"` | The database revision does not match the release in `.env.production`, usually after an interrupted release. Run `revision` (§2) and follow §5 and §6. |
| A page says it was loaded from an older release, or a save gets 409 | A new release is running: reload the page. |
| A save ends with a 502 or 504 message | The outcome is unknown. Check whether the change was saved before repeating it (Suspected duplicate, lost or uncertain write, below). |
| "Too many attempts from this computer" | The sign-in rate limit: wait one minute. Behind Cloudflare, several people can share the limit ([`SYNOLOGY_PUBLIC_HTTPS.md`](./SYNOLOGY_PUBLIC_HTTPS.md) §13). |
| A picture or an import is refused as too large, although it is within PartFlow's limits (pictures 2 MiB, import files 1 MiB) | The reverse proxy accepts smaller requests than PartFlow needs (at least 5 MiB). On the NAS, check the rule (`SYNOLOGY_NAS.md` §9.2 step 4) and read the active limit with `nginx -T 2>/dev/null \| grep client_max_body_size` (no line means nginx's default of 1 MiB). If DSM offers no setting, keep pictures below the limit and record the limitation. |
| The browser shows a certificate warning | The device does not trust the root certificate, the certificate has expired, or the address differs from the certificate's name ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §9.1). |
| `check.sh`: `FAIL https ... (curl 60)` | The NAS does not trust the certificate: add `--cacert <root certificate>` (option A). Behind Cloudflare, remove `--resolve-to`. |
| `check.sh`: `FAIL https host name ... not resolved (curl 6)` | The host cannot resolve its own PartFlow name: add `--resolve-to 127.0.0.1` (option A), or fix the DNS. |
| `check.sh`: `FAIL backup_age` | The scheduled backup did not run or failed: check its task and its emails, then run a manual backup (§3). |
| `check.sh`: `FAIL disk_...` | Database or storage pressure, below. |
| `check.sh`: `FAIL archival_proposal`, or a failure email of the archive task | §11: the line and the email name the finding or the step. |
| `archive.sh` exits with 4, or refuses with exit 1 because the incident record exists; or `check.sh`: `FAIL archival_proposal reconcile_after_purge` | The reconciliation after a purge has not ended clean: an incident, and the archive job works on no run until it is closed (Reconcile finding after a purge, below). |
| `archive.sh`: the pre-purge backup failed | Nothing was deleted; the next hourly start makes the run `FAILED`. Fix the cause (a backup that held the backup lock longer than `--backup-wait-minutes`, a stale backup lock (§3), too little space in the backup folder, a dump error), then request a new run (§11, The pre-purge backup). |
| `archive.sh`: a run `FAILED while purging` | Whole flow components only were deleted, and the purge receipt lists them; the reconciliation ran after it. Request a new run for the rest (§11, A failed run). |
| `archive.sh` exits with 2: "the purge of run N stopped", the run stays `PURGING` | A transient database error (a lost connection, a deadlock, a timeout, a server shutdown); the batch in progress was rolled back. Once the database answers, the next start resumes the purge and reconciles when it ends. If it keeps stopping, check the database (§2). |
| `check.sh` or `release.sh`: a lock is older than 4 hours, another release holds the lock, or `backup_lock_stale` | A killed run left its lock: §3, Backup lock and release lock. |
| `backup_running` | A backup, or a release holding the backup lock, is running: wait and run it again. |
| A scheduled task email says `HOME is not set` | Add `HOME=/root` at the start of the script part of the task's command ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.5). |
| `$PF` says `Set PARTFLOW_... in .env.production` | That value is empty in `.env.production`. |
| A secret path is a folder, or "is missing, empty or not a regular file" | Compose created a folder because the file was missing: `rmdir` it and create the file ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Secret files). |
| A container does not start: `NanoCPUs can not be set` | The host's kernel cannot limit processor use: set the four `PARTFLOW_*_CPUS` values to `0` ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §5 step 2) and start it again. |
| `release.sh`: the build inputs differ from the commit | `git status` lists changed or untracked files under `backend/`, `frontend/`, `deploy/production/` or the Compose files. Never edit the release checkout: undo the change (`git checkout -- <file>`) or delete the stray file, for example a DSM `@eaDir` folder, then run `release.sh` again. |
| `$PF up` fails with `No such image` | The images of the tag in `.env.production` are not on the host: correct `PARTFLOW_RELEASE`, or build the images ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Images and release identity). |
| No `Setup token` line in the log | Setup is already complete (an Administrator exists), or `backend` has not started. While no Administrator exists, `PARTFLOW_BACKEND_WORKERS=1 $PF up -d --force-recreate backend` writes a new token. |
| First-run setup answers `setup_token_invalid` | The token is old (every restart of `backend` writes a new one: use `$PF logs backend \| grep "Setup token" \| tail -n 1`), or `backend` runs several workers: start it with one worker ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Backend processes and first-run setup). |
| Nobody can sign in as an Administrator: forgotten password or login name | Sign-in and user accounts, below. |
| A user lost the authenticator app or passkeys of two-step sign-in and the recovery codes | Sign-in and user accounts, below. |
| Nobody may manage correction permissions any more (the backend logs a warning at startup) | Sign-in and user accounts, below. |

### Sign-in and user accounts

User accounts are managed in PartFlow, under **Administration → Users**, by a user whose role holds the permission
**Manage users and roles**. The commands of this section are for when nobody with that permission can sign in. Run
them on the host, in the release checkout, with `PF` set (Before you run any command).

**A forgotten password.**

- When another Administrator can sign in, they open **Administration → Users** and choose **Set password…** on the
  user's row. The user gets a temporary password, every sign-in of that user ends, and a lock is cleared.
- When nobody can sign in, for example because the only Administrator forgot the password, reset it with:

  ```sh
  $PF run --rm --no-deps backend python -m app.cli reset-password --login-name <login name>
  ```

  The command asks for the new password twice (`New temporary password`) and does not show what you type; it has no
  `-T`, because it needs the terminal for that prompt. The password is temporary: the user chooses a new one at the
  next sign-in when that option is on. Every sign-in of the user ends and a lock is cleared. Exit code 0 means done;
  1 means refused with nothing changed (an unknown login name, two different passwords, a password that breaks the
  password rules of Administration → Security — the message names each rule —, a user who never had a password, or no
  Administrator at all); 2 means the outcome is unknown or the database could not be reached: run it
  again, because it sets the password again either way. It works only while an Administrator exists and never
  creates one.

**A lost authenticator app or passkey (two-step sign-in).** A user who lost the app or a passkey signs in with
another one, with one of their recovery codes, or with a code PartFlow emails when the account has an email address
and an outgoing mail server is set up. They then remove the lost one and add a new one from the account menu
(`Two-step sign-in…`). A user who has none of these:

- When another user administrator can sign in, they choose **Turn off two-step…** on the user's row in
  **Administration → Users**. The user then signs in with the password alone. Keep **Sign them out everywhere** ticked
  when the lost device may be in someone else's hands; if the password may be known too, also choose
  **Set password…**.
- When nobody can sign in, for example because the only Administrator lost the app and the passkeys, turn it off
  with:

  ```sh
  $PF run --rm --no-deps -T backend python -m app.cli reset-two-step --login-name <login name> --end-sessions
  ```

  It deletes the authenticator app, the passkeys and the recovery codes. `--end-sessions` also signs the user out
  everywhere; leave it out to keep their sign-ins. Exit code 0 means done
  (or already off, which it says); 1 means refused with nothing changed (an unknown login name); 2 means the outcome
  is unknown or the database could not be reached: run it again.

A user locked after too many wrong codes waits for the lock to end (Security → User sign-in sets how long), or a
user administrator sets a new password, which clears the lock.

**A forgotten login name.** Read the login names of the users who may manage users and roles from the database. This
only reads; it changes nothing. Open the database shell:

```sh
$PF exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Paste this query, then leave with `\q`. Without the `JOIN role_permissions` and `WHERE` lines, it lists every user.

```sql
SELECT u.login_name, u.display_name, r.name AS role, u.is_active
FROM users u
JOIN roles r ON r.id = u.role_id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE rp.permission = 'MANAGE_USERS_AND_ROLES'
ORDER BY u.login_name;
```

Login names are always stored in small letters. Use the name you found to sign in, or to reset the password as
above.

**Changing a login name.** Open **Administration → Users**, click the user's row, change **Login name** in **Edit
user** and click **Save changes**. A login name has 1 to 128 characters, only letters `a`–`z`, digits and
`. _ @ + -`, and no spaces; letter case does not matter, because it is saved in small letters. It must not be in use
by another user. The password stays the same; from then on the user signs in with the new name. Never change a login
name directly in the database.

**Removing a user.** PartFlow never deletes a user, because its history keeps referring to them. To take away the
access, open the user in **Edit user**, turn **Active** off and click **Save changes**: every sign-in of that user ends
and the user can no longer sign in. Turn **Active** on again to give the access back.

- PartFlow refuses (`last_permission_holder`) a change of role or status that would leave no active user with a
  password who may manage users and roles, or correction permissions, so that it can never lock everyone out.
- For a user whose role holds a correction permission or **Manage correction permissions**, changing the role or
  **Active** also needs **Manage correction permissions**; without it, **Edit user** shows the role and the status
  as fixed.

**Nobody may manage correction permissions any more.** When no active user with a password holds **Manage
correction permissions** (the backend logs a warning at startup), give that permission back to a role that has an
active user with a password:

```sh
$PF run --rm --no-deps backend python -m app.cli restore-correction-permission-management --role-name <role>
```

It changes nothing else and refuses, with exit code 1, when a holder already exists, the role does not exist, or no
active user with a password has that role (root [`README.md`](../../README.md), Restoring the permission to manage
correction permissions).

**No Administrator left at all.** When no active user with a password may manage users and roles, `reset-password`
refuses and PartFlow opens first-run setup again: create a new Administrator with a new setup token, as
[`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §10 describes (with one backend worker, and the newest token from
`$PF logs backend | grep "Setup token" | tail -n 1`).

### Suspected duplicate, lost or uncertain write

- Stop the affected workflow if quantity integrity may be at risk.
- Keep the request time, Scan Station, User or Worker, PN, flow and `device_event_id`.
- Before any retry, follow the request in the backend log by `request_id`, `device_event_id`, PN, QuantityFlow, Scan
  Station and Worker (§2), and look at the server's result and history.
- Retry only with the original `device_event_id`, and only when appropriate.
- **Never edit Movement history directly.** Use the Undo or correction workflow only after the committed state is
  known.

### Guard-integrity finding (reconcile check (h))

- Freeze writes (`$PF stop backend`, §5) when a trigger or a guard function changed, a trigger is disabled, or
  `session_replication_role` is set database-wide or active: the guards then do not fire for any session,
  `partflow_app` included.
- For that setting, the owner runs `ALTER DATABASE <db> RESET session_replication_role` inside `db`.
- Compare the triggers and functions with the migration source. The owner decides the repair and whether history
  must be checked against the last backup.
- Before reopening writes, run `reconcile` and the privilege probe below. It tries one forbidden statement as
  `partflow_app` and rolls it back; repeat it for each append-only table and statement (`UPDATE`, `DELETE`,
  `TRUNCATE`). A refusal prints `permission denied for table <table>` on standard error, which is the expected
  result; `UPDATE 0` without an error means that the protection is missing.

  ```sh
  $PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -c BEGIN -c "SET LOCAL ROLE partflow_app" -c "UPDATE part_movements SET id = id WHERE false" -c ROLLBACK'
  ```

### Reconcile finding after a purge

Right after every purge of Movement history, `archive.sh` runs the full reconciliation (§11). When it does not end
clean (a finding, an error, or no complete report), `archive.sh` exits with 4, which sends the failure email, and writes
the incident record `<records-dir>/archive/reconcile-after-purge`: the run, the result, the failing checks and the path
of the report. `check.sh` then reports `FAIL archival_proposal reconcile_after_purge` until the record is removed. While
the record exists, the job fails closed: every start runs only `recover` and `status`, works on no run (no export,
verification, copy, pre-purge backup or purge, not even the resumed purge of a run left `PURGING`) and exits with 1,
so the history archive task sends one failure email per hour until the incident is closed. The purge may have damaged
history or quantities, so treat it as an incident:

1. **Stop requesting runs.** The job itself works on no run while the record exists. Tell everyone who holds **Run
   history archival and purge** not to request a run, and cancel a run that was requested meanwhile (Cancel in
   Administration → History archival & purge, possible while a run is `APPROVED` or `WAITING_FOR_COPY`). To stop the
   hourly failure emails, disable the history archive task in the host scheduler (on the NAS: untick **Enabled** in
   Task Scheduler). Keep the pre-purge backup, the archive folder and the purge receipt: they are the evidence and the
   way back.
2. **When the reconciliation could not run or ended in error**, run it again by hand (§7, Running it). When it is clean,
   the purge is confirmed: close the incident (step 5). When it has findings, go on.
3. **Compare the findings with the receipt.** The report `<records-dir>/archive/<UTC>/reconcile.json` (its path is in
   the incident record and in the email) names each finding's check, entity and Part Number. The purge receipt
   `<archive-dir>/<archive name>.purged.json` lists every component of the run with its outcome (`PURGED`, `SKIPPED`
   or `RETAINED`), its Part Numbers, its flows and its Movement count. These commands list the Part Numbers of the
   purged components and those of the findings (never attach a report to a ticket or email: check (j) findings may
   hold Worker badge values):

   ```sh
   jq -r '.components[] | select(.outcome == "PURGED") | .part_numbers[]' "<archive-dir>/<archive name>.purged.json" | sort -u
   jq -r '.checks[] | select(.status != "pass") | .id + " " + .title' "<report>"
   jq -r '.checks[].findings[] | .part_number // empty' "<report>" | sort -u
   ```

   Compare also with the last scheduled reconciliation before the purge (`~/partflow-monitoring/reconcile/`). A finding
   that was already there, on a Part Number that the receipt does not list as `PURGED`, did not come from the purge: it
   is an existing incident (§7, Operating rules). A new finding on a purged Part Number, or any finding of check (g),
   points at the purge.
4. **The owner decides: fix forward, or restore the pre-purge backup.**
   - Fixing forward keeps every change made since the purge. Never edit history or stored totals by hand (§7,
     Operating rules); the owner decides each repair.
   - Restoring the pre-purge backup brings the purged rows back, but **discards every change recorded since its dump
     started** (`dump_started_at` in the backup's `manifest.json`, also `pre_purge_backup.dump_started_at` in the
     receipt), because production went on during and after the purge. Choose it only when the findings show that the
     purge damaged history or quantities that cannot be corrected forward, and the owner accepts that loss; record the
     decision and the lost time window.

   **Which backup.** Restore the pre-purge backup that the receipt names, never a later backup, and disable the
   history archive task first if step 1 did not (§6, Restores and the history archive job). A later backup, such as
   the hourly backup taken while the purge ran, holds the run in `PURGING`, and the job resumes that purge at its first
   start once the incident record is removed and the task is enabled. The pre-purge backup holds the run in `RUNNING`:
   the next start fails it, and nothing is purged again.

   **How to restore it.** Follow the Path 3 procedure (§6) with these changes. Instead of step A's release-record
   guard, stop writes (`$PF stop backend`, and check that `$PF ps --status running -q backend` prints nothing), then
   verify the backup with `(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)` and
   `$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"`, where
   `<NAME>` is the pre-purge backup that the receipt names. Run B1 with `BPATH=<backup-dir>/<NAME>`, and B2 with `REC`
   set to a new incident folder that you create first (`mkdir <records-dir>/<UTC>-restore-<NAME>`); record the owner's
   approval there in `restore.json` (the fields of `rollback.json`) before B3; run B3 with `PREV` set to the running
   release, the tag in `.env.production`: the release does not change, and the checkout stays where it is. The purged
   database keeps its name and content for the analysis.

   **After the restore** the database holds the run in `RUNNING`, as it was when the backup was taken. Run
   `deploy/production/pf archive` once: its `recover` makes the run `FAILED` ("…also after a restore of its pre-purge
   backup). Nothing was deleted; request a new run."), so it is never purged again; a failed run is final, and only a
   new request starts a new run, with its own export, copy and pre-purge backup. `archive.sh` exits with 3 for that run
   and says "Nothing was deleted in this database", which is true of the restored database: it holds every purged
   Movement again. Because the incident record still exists, that start works on no other run. `check.sh` reports
   `last_run_failed` for that run until a later run finishes.

   **What still reports the purge.** The archive folder `<archive-dir>/<archive name>/`, its receipt
   `<archive name>.purged.json` (state `COMPLETED` or `COMPLETED_WITH_SKIPS`, result `PURGED`) and their copies in the
   copy folder stay as evidence: leave them in place. `recover` and `status` read only the receipts of runs that
   recorded a receipt SHA-256, and the restored run has none, so nothing reports that this receipt now describes the
   database that the restore replaced: write that down in the incident folder, beside `restore.json`. A later run
   archives the same history again into a new folder. Run numbers can repeat after a restore; the time in a folder's
   name tells the folders apart.
5. **Close the incident.** Once the owner's decision is carried out and the reconciliation (§7) is clean, check the
   runs in Administration → History archival & purge (a run in `PURGING` resumes its purge at the next start), remove
   the incident record, and only then enable the task again if you disabled it:

   ```sh
   rm ~/partflow-deployments/archive/reconcile-after-purge
   ```

   Only removing the record closes the incident: the next start works on runs again, for example the resumed purge of
   a run left `PURGING`. The reports stay in their record folders.

### Database or storage pressure

- Block new writes before the disk is full (the write freeze, §5), and keep the logs and figures.
- The storage alert is a `check.sh` line `disk_*` below 15 % free: `disk_data` for the database volume, `disk_backup`
  for the backup folder, `disk_docker` for the Docker storage (images and container logs), `disk_archive` for the
  archive folder.
- **Do not delete PostgreSQL files, volumes, Movement rows or backups to make room.**
- Space rules: a backup needs twice the newest dump plus a reserve free in the backup folder; a path 3 restore needs
  the database size plus twice the dump plus 1 GiB on the database volume (§6); a stale backup lock blocks backups
  and releases until it is removed (§3).
- Expand the storage. History archival & purge (§11) deletes old Movement history from the database, but that frees
  rows inside the table for new Movements, so the database stops growing without its files getting smaller; the disk
  space comes back only with `VACUUM FULL part_movements`, which needs free space as large as the table (§11,
  Returning disk space after a purge). A purge also adds a pre-purge backup to the backup folder.
- Run the reconciliation before reopening writes.

### Host failure

- Make sure the failed host cannot accept writes: two live copies must never exist.
- Prepare a recovery host and restore the newest verified backup with the matching release (§6, New-instance
  restore).
- Run the reconciliation and the smoke checks.
- Record the data-loss window: the writes made after the backup's `dump_started_at`.
- Point the clients to the recovery host only after the owner's approval is recorded in `restore.json`.

## 9. Routine schedule

These tasks are recommended after go-live ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5); none of them blocks use. The
owner chooses the retention values, the schedules and the acceptable loss and recovery times; the values here are
working examples.

| When | Tasks |
| --- | --- |
| Every 15 minutes (automatic) | `check.sh`: health, restarts, disks, certificate, backup age, backend errors and the archival status (§2). An unchanged failure is repeated every 6 hours. |
| Every 5 minutes (automatic) | `archive.sh --scheduled`: once an hour, at the Start minute of Administration → History archival & purge, the history archive job works on a requested run: export, verification, off-host copy, pre-purge backup, purge and the reconciliation after it (§11). |
| Daily | The scheduled backups (every hour) and the scheduled reconciliation at 04:00 run by themselves. Read every failure email, including those of the off-host copy; exit 4 of a backup includes a scheduled or pre-purge backup that fails verification, and exit 4 of the history archive job is an incident (§8). |
| Weekly | Review the database growth (`~/partflow-monitoring/growth.tsv`) and `docker stats --no-stream`. Review failed sign-ins and refused permissions: `$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'` (`refusal.type` and `refusal.code` tell a refused or locked sign-in, a permission refusal, `station_device_required`, `station_device_mismatch` and `csrf_rejected` apart). Check pending security updates of the host. |
| Monthly | Confirm the off-host copy of every archival run that waits for it (§11). Remove old reconciliation reports after review. Remove pre-release and manual backups whose observation time has ended, and scheduled or pre-purge backups that the rotation reported invalid, after review (below). Install updates, on a test system first where possible. Review users and roles, firewall rules and secrets. Run `reconcile --check h`. |
| Quarterly, and after a large schema change | The restore drill (§4); compare its `restore_to_ready` and `total` times with how long the factory can work without PartFlow. |
| Before a change of the PostgreSQL image or of the host's glibc | `restore-test.sh --backup <latest> --db-image <candidate>` (§4, §7). |
| Before every release | Review the migrations, the rollback decision and the checks (§5); `release.sh` takes the backup. |

**Removing old backups and reports.** No tool deletes pre-release backups, manual backups, scheduled or pre-purge
backups that fail verification, or reconciliation reports; the rotation keeps the newest pre-purge backups by
itself (§3). After review, remove each one by name; never remove the newest verified scheduled backup, the pre-purge
backup of an open incident (§8, Reconcile finding after a purge) or anything in an archive folder:

```sh
rm -r "<backup-dir>/<NAME>"
rm ~/partflow-monitoring/reconcile/<report file>
```

**Changing a password.** Before you recreate `backend`, run
`deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` (recreating `backend` deletes its
log, §2).

- **A role password** (`partflow_app_password` or `partflow_maintenance_password`), in a short write freeze, when no
  import runs. Replace the file (the rules are in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Secret files), then let
  `db-roles` set the new password and recreate `backend`:

  ```sh
  openssl rand -base64 32 > "<secrets-dir>/partflow_app_password.new"
  mv -f "<secrets-dir>/partflow_app_password.new" "<secrets-dir>/partflow_app_password"
  chmod 0444 "<secrets-dir>/partflow_app_password"
  $PF stop backend
  $PF --profile ops run --rm -T db-roles
  $PF up -d --force-recreate --no-deps backend
  ```

  Then check health (§2). A plain `up -d backend` does not pick up the new password, because the container is not
  recreated, and running `db-roles` while `backend` serves makes its new connections fail. For
  `partflow_maintenance_password`, replace that file and run only the `db-roles` command while the history archive
  job does not work (no `~/partflow-deployments/.archive.lock`), away from its Start minute: only that one-shot job uses
  the role (§11), so no freeze is needed. A purge that can no longer reach the database stays `PURGING`, and the next
  start resumes it.
- **The owner password** (`postgres_password`): change it in the database first, then replace the file. No restart
  is needed, because only the one-shot `migrate` and `db-roles` read it.

  ```sh
  openssl rand -base64 32 > "<secrets-dir>/postgres_password.new"
  cat "<secrets-dir>/postgres_password.new"        # copy this value
  $PF exec db psql -U <POSTGRES_USER> -d <POSTGRES_DB>
  ```

  In `psql`, type `\password <POSTGRES_USER>`, paste the copied value twice, then `\q`. Finally:

  ```sh
  mv -f "<secrets-dir>/postgres_password.new" "<secrets-dir>/postgres_password"
  chmod 0444 "<secrets-dir>/postgres_password"
  ```

## 10. Deleting test data permanently

Production history is append-only: PartFlow itself never deletes a Work Order that has production history or an
allocation, and deletes Movements only through the purge of History archival & purge, which removes archived Movement
history once its archive is copied off the host and a pre-purge backup is taken (§11;
[`../PROJECT_PROFILE.md`](../PROJECT_PROFILE.md), Administrative Archival and Purge). The command
`pf delete-permanently` is the other exception, and it is meant for one case only: Work Orders and Part Numbers
that the owner entered **only for testing** in production. It deletes them as if they had never been entered and
keeps one audit row for each deleted Work Order and Part Number. It is an interim tool: a delete inside PartFlow, for a
Super User, will be designed later together with the history archival and purge. Never use it to correct real
production data; use the corrections that PartFlow offers instead.

### Preview, then confirm

1. Show what would be deleted. Nothing is changed:

   ```sh
   deploy/production/pf delete-permanently --work-order "<Work Order Number>" --part-number "<Part Number>" \
       --reason "<why the data is deleted>"
   ```

   Repeat `--work-order` and `--part-number` for every record; give at least one of them. Write the Work Order Number
   exactly as PartFlow shows it, including spaces and capital letters: it is matched exactly. The Part Number is
   normalized as PartFlow normalizes every Part Number (surrounding spaces removed, capital letters). `--reason` is
   required and is recorded; `--operator` defaults to your login name, as in the other `pf` commands.
2. Read the report that the command prints on the standard output (one JSON document): `work_orders` and
   `part_numbers` name the records, `rows` gives how many rows of each table would be deleted, `kept_audit_rows` lists
   the audit rows that are kept because they also concern records that stay, and `refusals` lists every reason why
   the deletion cannot run.
3. Delete: run the same command again with `--confirm`. `pf` then runs these steps and stops at the first one that
   fails:
   1. the preview again; a refusal stops here, before any backup;
   2. a verified manual backup, `backup.sh --kind manual` with the reason `before pf delete-permanently` (§3);
   3. the deletion in **one** database transaction, with the name of that backup recorded in the audit rows.

The command runs as the database owner role, in the one-shot `db-roles` service, because the application database
role can never delete history. Without `pf`, take the backup first (§3), then run
`$PF --profile ops run --rm --no-deps -T db-roles delete-permanently <the same options> --operator "<your name>" --confirm --backup-name <NAME>`.
The deletion locks the tables it changes for a moment: PartFlow keeps showing pages, and a write that arrives meanwhile
waits until the deletion has finished (`--lock-timeout`, 30 seconds by default, limits how long the deletion itself
waits for a lock). Run it at a quiet moment.

### What is deleted, and what stays

| Deleted for a Work Order | Deleted for a Part Number |
| --- | --- |
| The Work Order, its demand lines, every allocation of those lines, every Quantity Flow released for those lines and every flow linked to them by a split or a combine, every Movement and lineage record of those flows, the Assigned Route copies of those flows with their steps, and the audit rows that concern only these records. | The Part Number record with its details and image, every Quantity Flow of the Part Number with the same closure, every demand line and allocation of the Part Number, and the audit rows that concern only these records. Every Work Order with a line of the Part Number must be deleted in the same command; an internal Work Order (one without a Work Order Number, created by a Scan Station receipt) whose every line is of a Part Number being deleted is deleted with it. |

Master data stays: Departments, Areas, Operations, Machines, Workers, Worker Sessions, Scan Stations, Route Templates
and the Part Number records that were not named. A deleted Work Order Number can be entered again afterwards.

**The one audit row.** For each deleted Work Order and Part Number the command adds one audit row (event `DELETED`)
with the last values of the record, and in its metadata (`permanent_deletion`) the operator, the reason, the backup,
the names of every record deleted together, and the number of rows deleted from each table; its time is the time of
the deletion. An audit row that also concerns records that stay (for example a Hot list change that also moved a line
that stays) is kept unchanged.

**The deleted data stays in the backups.** Every backup taken before the deletion, including the one `pf` takes
first, and every off-host copy of those backups still holds the deleted data until it ages out under the retention of
§3. Restoring such a backup brings the data back.

### Refusals

The command refuses, deletes nothing and names each link when the data to delete is linked to data that stays:

| Code | Meaning | What to do |
| --- | --- | --- |
| `unknown_work_order`, `unknown_part_number` | No Work Order has this exact number, or the Part Number has neither a record nor any data. | Check the spelling. |
| `part_number_used_by_work_order` | A Work Order that is not being deleted has a demand line of the Part Number. | Delete that Work Order in the same command too, or keep the Part Number. |
| `on_hot_list` | A demand line to delete is on the Hot list; deleting it would leave a gap in the ranks of the lines that stay. | Take it off the Hot list in PartFlow first. |
| `lineage_link` | A split or a combine links a flow to delete with a flow that stays (released for a Work Order that stays, or quantity added without a Work Order). | Delete that Work Order in the same command, or keep the data. |
| `stock_needed` | The stocked quantity of the flows to delete is still needed by the allocations of Work Orders that stay. | Reverse those allocations in PartFlow first, or delete those Work Orders too. |
| `allocation_command_shared`, `allocation_reversal_link` | One allocation, or its reversal, also concerns a demand line that stays. | Delete that Work Order in the same command, or keep the data. |
| `movement_command_shared`, `movement_reversal_link`, `route_snapshot_shared`, `movement_references_deleted_data` | One production command, an Undo, an Assigned Route copy or the record of a Movement also concerns a flow that stays. | Delete the Work Order of that flow in the same command, or keep the data. |
| `archived_history` | The history of a flow to delete was archived by an archival run (§11), and perhaps purged: the command never deletes archived history. | Keep the data. |
| `owner_role_required` | The command does not run as the database owner role. | Run it through `pf` or the `db-roles` service. |

### Exit codes

| Exit | Meaning |
| --- | --- |
| 0 | The preview was shown, or (with `--confirm`) the records were deleted. |
| 1 | Refused: nothing was deleted, and with `--confirm` no backup was taken. |
| 2 | Could not run or failed: nothing was deleted (for example the backup failed, the database could not be reached, or a table lock was not granted in time). When the database connection failed while committing, the report says `outcome_unknown`: run the preview again to see which records still exist. |

## 11. History archival

PartFlow moves old Movement history out of the database into archive files and then deletes it from the database:
this is History archival & purge ([`../PROJECT_PROFILE.md`](../PROJECT_PROFILE.md), Administrative Archival and
Purge). A run exports the Movement history of closed flows that is older than its cutoff into an archive folder,
verifies the archive, copies it off the host, takes a pre-purge backup of the database, and then **purges**: it
deletes the archived Movements from the database and keeps one carry-forward record per archived flow, so that every
quantity, status and check stays exact. Once purged, that history is in the archive folder and its off-host copy (and
in the pre-purge backups that are still kept), no longer in the database; commands whose history was archived can no
longer be undone. The automatic runs, the deletion and viewing requests and the optional groups are later build steps.

A user who holds the permission **Run history archival and purge** sets the retention period and the other settings,
looks at a preview and requests a run in **Administration → History archival & purge**; the request is the approval.
The web application never touches the archive folder. The **history archive job** on the host does the work.

### What the archive job does

A host task starts `deploy/production/archive.sh --scheduled` every 5 minutes ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md)
§11.8). Once an hour, at the first start at or after the **Start minute** set in Administration (20 initially), it
does the **hourly work**; every other start ends within seconds and prints `NOT_DUE`. The first start after the task
is created only records the current hour, so the first hourly work comes at the next Start minute.
`deploy/production/pf archive` runs the hourly work at once, whatever the time.

The hourly work runs these steps in the one-shot `maintenance` service, which connects as the maintenance database role
(`partflow_maintenance`, which reads history, writes the archive records and may delete Movements only through the
purge: the row guard `trg_part_movements_guard_delete` admits exactly the archived rows of a run in `PURGING`) and is
the only service that mounts the archive folder:

1. `recover`: a run left `RUNNING` by a job that stopped (a restart, a power loss), or whose pre-purge backup failed,
   becomes `FAILED`; unfinished folders and receipt files of finished runs are removed from `.partial/`; and a purge
   receipt that is missing in the copy folder is copied there again. A run left `PURGING` is not touched: its purge
   resumes below. When `recover` does not end with exit 0, no run is worked on in that start (only `status` runs).
   Neither while the incident record `<records-dir>/archive/reconcile-after-purge` exists (§8, Reconcile finding after
   a purge): the start then runs only `recover` and `status`, works on no run, not even the resumed purge of a run left
   `PURGING`, and exits with 1 (with 3 when `recover` failed a run).
2. `next`, again after every step: the run to work on and its next step. All of a run's steps can happen in one start.
3. The step that `next` names:
   - `export` writes the archive into `<archive-dir>/.partial/`. With nothing eligible, the run ends as
     `NOTHING_ELIGIBLE`.
   - `verify` re-reads every file, compares it with the database and publishes the folder
     `<archive-dir>/run-<id>-<UTC time>/`. A component that changed since the export is skipped and stays in the
     database; when every component changed, the run ends as `COMPLETED_WITH_SKIPS` with the result `NOTHING_PURGED`,
     without a copy or a backup.
   - `copy` copies the archive to the off-host copy folder and checks the copy, or accepts a copy confirmation; without
     either, the run waits in `WAITING_FOR_COPY` (The off-host copy, below).
   - `backup`: the pre-purge backup (below). `next` hands it out only after checking that the User who requested the
     run is still active and still holds Run history archival and purge; otherwise the run becomes `FAILED` and nothing
     is deleted.
   - `purge` with that backup: the run becomes `PURGING`, and its archived Movements are deleted in batches of whole
     flow components, at most **Movements per purge batch** and **Part Numbers per purge batch** of Administration
     (50,000 and 20 initially). Each batch is one transaction under the locks of its Part Numbers; a component that
     changed since the export is skipped and stays. The purge receipt `<archive-dir>/<archive name>.purged.json` is
     written (and copied to the copy folder), and the run ends as `COMPLETED` (result `PURGED`) or
     `COMPLETED_WITH_SKIPS`. A transient database error while purging (a lost connection, a deadlock, a serialization
     failure, a lock or statement timeout, a server shutdown) rolls back the batch in progress and leaves the run
     `PURGING`; the purge exits with 2. A run left `PURGING` by an earlier start (such an error, or a restart) is
     resumed by `purge` alone, without a new backup: only whole components are ever deleted. Every other failure while
     purging (a deleted count that differs from the records, a guard refusal, a changed published archive, an internal
     error) makes the run `FAILED`, with its receipt (A failed run, below).
4. After a purge that ended, or failed once the run was `PURGING`: the full reconciliation (§7), in a one-off `backend`
   container as the application database role. When it does not end clean, it is an incident (§8, Reconcile finding
   after a purge). A purge that exits with 2 is not reconciled in that start: the next start resumes it and reconciles
   when it ends. A start never begins another run after a purge.
5. `status`: the open run, the last finished run and the free space, for the monitoring.

Production goes on during a run: the export reads one database snapshot, and each purge batch holds the locks of its
Part Numbers only for its own transaction. A first large run can take long; the next starts then wait. A changed
Start minute applies from the next hourly work. **Choose the Start minute** away from the hourly backup at minute 0,
because the pre-purge backup waits for a backup that is running, and away from the times you install releases: a
release is refused while the job works, and a purge can take long.

**Exit codes of `archive.sh`.** The host task's failure email is the alert. On success it prints one line:
`NOT_DUE`, `DEFERRED archive_running` (the previous start still works on its hourly work), or
`ARCHIVE <outcome> <run> <state>`, for example `ARCHIVE completed 3 COMPLETED`.

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Done, or nothing was due. | Nothing. |
| 1 | Refused, nothing changed: a release runs, another `archive.sh` holds the archive lock, another history-archive command runs, or the incident record of a reconciliation after a purge exists. | Nothing: the next hour's start tries again. Repeated refusals: Locks, below; the incident record: §8, Reconcile finding after a purge. |
| 2 | Could not run: the configuration, Docker or the database, or a step whose outcome is unknown; or the pre-purge backup failed; or a purge stopped on a transient database error (the run stays `PURGING`). | Read the email; fix the cause. A run left `RUNNING` is failed by the next start's `recover` (then request a new run); a run left `PURGING` is resumed by the next start, which reconciles when the purge ends. |
| 3 | A run failed: it is now `FAILED`. | A failed run, below. |
| 4 | The reconciliation after a purge found a mismatch, ended in error or could not run. | An incident: §8, Reconcile finding after a purge. Until its record is removed, every start refuses to work on a run (exit 1). |

`archive.sh --help` lists its options; `--backup-wait-minutes N` (0 to 1440, 60 by default) is how long the pre-purge
backup waits for another backup. A scheduled start reports a refusal or a failure once for its hour, never at every
5-minute start. The reports of an hourly work that did something or failed are in `<records-dir>/archive/<UTC>/` (§1).

**Locks.** `archive.sh` holds `<records-dir>/.archive.lock` (its file `owner` names the host, the process id (pid),
the start time, `archive.sh`, the kernel's boot id and the process's start time as the kernel counts it) only during the hourly work, never while a run merely waits for its copy
confirmation and never for a start that only checks the time. It holds it for the whole hourly work: the wait for the
backup lock, the pre-purge backup, the purge and the reconciliation after it included. It refuses while a release runs
(`<records-dir>/.release.lock`), and `release.sh` refuses, changing nothing, while the archive lock is held: start the
release again a few minutes later. Releases are best started a few minutes after the full hour and well before the
Start minute. While the lock's owner still runs, a scheduled start ends with `DEFERRED archive_running` (exit 0) and
a manual start (`pf archive`) is refused. A lock whose owner provably no longer exists is removed by the next `archive.sh` automatically: same host, no
`maintenance` container running, and either its pid is gone, or the host restarted since (another boot id), or the pid
now belongs to another process (another start time). `release.sh` ignores such a lock by the same rule. The lock is
first renamed, so that of two starts only one goes on. An owner file without the boot id and start time (written by an
earlier `archive.sh`), or a host without `/proc`, is judged by the pid alone. When a refusal says that the lock cannot
be checked, or when scheduled starts keep printing `DEFERRED` although no archive job runs, check and remove it by
hand:

```sh
cat ~/partflow-deployments/.archive.lock/owner
ps -ef | grep '[a]rchive\.sh'                        # must print nothing
docker ps --filter label=com.docker.compose.service=maintenance   # must list no container
rm -r ~/partflow-deployments/.archive.lock
```

### Checking a run

- **Administration → History archival & purge** lists every run with its state, its counts and, once exported, the
  archive name and the manifest SHA-256.
- `check.sh` prints the line `archival_proposal` (§2): `PASS` with the open run (a `PURGING` run with what it purged so
  far), the last finished run (its result, what it purged and what it skipped) and the free space, or `FAIL` naming
  each finding: `copy_confirmation_overdue` (a run has waited for its copy confirmation longer than the setting
  **Report a waiting run after**), `last_run_failed` (the newest finished run is `FAILED`), `archive_dir_unusable` (the
  archive folder is missing or not writable), `copy_dir_unusable` (the copy folder is set but not mounted, missing,
  not writable or on the archive folder's own file system) or `receipt_not_copied` (the purge receipt of a run that the
  job copied off the host is missing or differs in the copy folder; the next start copies it again). The incident of a
  reconciliation after a purge comes first, as `reconcile_after_purge` (§8).
- The full report, read-only:

  ```sh
  $PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" maintenance status
  ```

| State | Meaning |
| --- | --- |
| `APPROVED` | Requested in Administration; the next hourly work exports it. It can still be cancelled. |
| `RUNNING` | The job exports, verifies or copies it, or takes its pre-purge backup. It cannot be cancelled. |
| `WAITING_FOR_COPY` | Verified and published; it waits for its off-host copy or a confirmation. It can be cancelled. |
| `PURGING` | The job deletes its archived Movements, batch by batch. It cannot be cancelled; a purge that stopped (a restart, or a transient database error such as a lost connection or a deadlock) is resumed by the next start. |
| `COMPLETED` | Copied off the host and purged: the result is `PURGED`. A run finished before the purge existed has the result `EXPORT_ONLY` (exported, nothing deleted). |
| `COMPLETED_WITH_SKIPS` | Finished, but some components changed after the export and stay in the database; the receipt lists them. The result is `PURGED`, or `NOTHING_PURGED` when every component was skipped. `check.sh` passes and names the skips. |
| `NOTHING_ELIGIBLE` | Nothing was eligible at its cutoff when the export started. |
| `CANCELLED` | Cancelled in Administration. A published archive folder stays. |
| `FAILED` | A check failed; the reason is shown. Nothing was deleted, unless the run failed while `PURGING` (a deleted count that differs from its records, a guard refusal, a changed published archive, an internal error; a transient database error leaves it `PURGING` instead): it then keeps the whole components it purged, and its receipt lists them. |

### The pre-purge backup

Right before a purge, `archive.sh` takes a backup of the kind `pre-purge`:
`deploy/production/backup.sh --kind pre-purge --label <archive name> --operator archive.sh` (§3), named
`<UTC stamp>-pre-purge-<archive name>`. It is published and verified like every backup, and the purge records its
name and the time its dump started. Restoring it brings back the history that the purge deleted (§8, Reconcile
finding after a purge).

- When another backup holds the backup lock (the hourly backup, a manual one), the pre-purge backup waits for it,
  checking every 15 seconds, for at most `--backup-wait-minutes` of `archive.sh` (60 minutes by default; `0` refuses
  at once). A stale backup lock is refused at once (§3, Backup lock and release lock).
- When it fails (a backup that held the lock for longer, a stale lock, too little space, a dump error), nothing was
  deleted: the run stays `RUNNING`, the next hourly start makes it `FAILED` (`recover`), and a new run must be requested
  in Administration. `archive.sh` exits with 2. This is on purpose: a database restored from a pre-purge backup also
  holds its run in `RUNNING`, and `recover` must fail it so that it is never purged again.
- Pre-purge backups take space in the backup folder, one dump each. The rotation of the scheduled backups keeps the
  newest **Pre-purge backups kept** of Administration → Backups (3 initially, 1 to 100) and deletes the older ones (§3,
  Retention).
- The off-host copy of the backup folder (Hyper Backup on the NAS) must cover them like every backup.
- Once a run is purged, its archive folder and the archive's off-host copy are the only copies of that history, apart
  from the pre-purge backups that are still kept: keep both copies as carefully as the backups.

### Returning disk space after a purge (optional)

A purge frees the rows of the deleted Movements inside the table: PostgreSQL's automatic vacuum lets new Movements
reuse that space, so the database stops growing, but its files on the disk do not get smaller. Only
`VACUUM FULL part_movements` returns the space to the disk. It writes a new copy of the table and its indexes, **locks
the table exclusively while it runs** (every page, scan and command that reads or writes Movements waits), and needs
free space on the database volume as large as the table with its indexes. Run it only when the disk space is needed,
with the application stopped and after a backup:

1. Choose a quiet time. Check that the history archive job does not work (no `~/partflow-deployments/.archive.lock`),
   and disable its task until the end.
2. Compare the size of the table with its indexes (first command, in KiB) with the free space of the database volume
   (`Available` of the second command, in KiB); the free space must be larger:

   ```sh
   $PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_total_relation_size('"'"'part_movements'"'"') / 1024"'
   $PF exec -T db df -Pk /var/lib/postgresql/data
   ```

3. Stop the application, which is the write freeze (§5): `$PF stop backend`.
4. Take a backup: `deploy/production/backup.sh --kind manual --operator "<your name>" --reason "before VACUUM FULL"`.
5. Run it; this can take long on a large table:

   ```sh
   $PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM (FULL, VERBOSE, ANALYZE) part_movements"'
   ```

6. Start the application again (`$PF up -d backend`), check health (§2) and run the reconciliation (§7). Enable the
   history archive task again.

### The off-host copy and its confirmation

An archive that stays on the host is lost with the host, so every run copies its archive off the host before it is
purged. Two ways exist:

- **With a copy folder** (`PARTFLOW_ARCHIVE_COPY_DIR`, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1): a folder on another
  device, such as a network share or a USB disk mounted on the host. The job copies the archive there, re-reads the
  copy, checks every SHA-256 and goes on with the pre-purge backup and the purge, unattended; after the purge it copies
  the purge receipt there too and checks it. `archive.sh` mounts the copy folder only while it
  exists, is writable, and lies on a file system that is neither the archive folder's nor the host's root file system
  `/`. It checks this again before every step, so a disk or share that drops out during a long export is noticed before
  the copy, and the mount never creates a missing folder. When a disk or share is not mounted, its path is an empty
  folder on whichever file system holds the mount folder, often `/`; the job then copies nothing, says why in its
  output, and the run waits. Once the folder is available again, the next hourly work copies the archive.
- **Without a copy folder**, the run waits in `WAITING_FOR_COPY`:
  1. Copy the folder `<archive-dir>/<archive name>` off the host. On the NAS, the archive folder is part of the
     Hyper Backup task ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.3): wait until its next successful run. Otherwise
     copy it with `scp -r` or to a USB disk.
  2. Check the copy where it is now (Checking an archive, below).
  3. In Administration → History archival & purge, compare the manifest SHA-256 shown for the run with the copy's
     (`sha256sum manifest.json`), then choose **Confirm copy…** on the run, tick **The archive folder is copied
     off the server** and choose **Confirm copy**.
  4. The next hourly work goes on with the run: the pre-purge backup and the purge. The job copies no purge receipt
     for such a run: the receipt lies beside the archive folder, so the next run of the Hyper Backup task copies it
     off the host with the folder.

A run that waits for a confirmation longer than the setting **Report a waiting run after** of Administration
(7 days initially) makes
`check.sh` report `FAIL archival_proposal copy_confirmation_overdue`. A waiting run never blocks a release.

### A failed run

A run becomes `FAILED` when a check fails: for example the cutoff became newer than the shortest allowed period, the
counts exceed the preview, the files differ from the database, the published folder changed or vanished, the User who
requested it is no longer active or no longer holds Run history archival and purge, or the job stopped while the run
was `RUNNING` (also after a failed pre-purge backup). `archive.sh` then exits 3 once, and `check.sh` reports
`last_run_failed` until a later run finishes.

1. Read the reason: in Administration, in the failure email, or in `<records-dir>/archive/<UTC>/` (the step's
   `.json` and `.log`).
2. Fix the cause, for example free space in the archive or backup folder, or mount the copy folder.
3. Request a new run in Administration. A failed run is never resumed; the new run writes a new archive folder.

Nothing was deleted, unless the run failed while `PURGING` (a deleted count that differs from its records, a guard
refusal, the published archive changed, an internal error): it then keeps the whole components it purged, its receipt
`<archive name>.purged.json` (state `FAILED`) lists them as `PURGED` and the rest as `RETAINED` or `SKIPPED`, and the
reconciliation ran after it as after every purge. A new run archives and purges the rest. A transient database error
while purging never fails the run: it stays `PURGING`, and the next start resumes it (What the archive job does,
above). A folder that a failed run
already published stays as it is; keep it, and never remove the folder or the receipt of a run that purged anything.
`recover` removes the unfinished folders and receipt files under `.partial/` of finished runs and keeps any entry
whose name belongs to no run; such an entry is named in the output: review and remove it by hand. Never change or
remove a published folder or a purge receipt of a `COMPLETED` or `COMPLETED_WITH_SKIPS` run.

### Checking an archive

Each archive folder holds `movements.jsonl.gz`, `flows.jsonl.gz`, `lineage.jsonl.gz`, `assigned_routes.jsonl.gz`,
`context.json`, `manifest.json`, `README.txt` and `SHA256SUMS`. Standard tools alone check it, wherever the folder is
(the archive folder, the copy folder or a restored copy):

```sh
cd <archive-dir>/run-<id>-<UTC time>
sha256sum -c SHA256SUMS
```

Every line must end with `OK`. The SHA-256 of `manifest.json` (`sha256sum manifest.json`) is the manifest SHA-256 that
Administration shows. The files read with `zcat` and `jq`; `README.txt` describes them. Archived history is never
imported back into its live tables.

Beside the folder of each purged run lies its **purge receipt** `<archive-dir>/run-<id>-<UTC time>.purged.json` (mode
0600; also in the copy folder for a run that the job copied there): one line of JSON, read it with `jq .`. It names the
run, its result and its pre-purge backup, and lists every component with its outcome (`PURGED`, `SKIPPED` or
`RETAINED`), its Part Numbers, flows and Movement count. Administration shows its SHA-256, which `sha256sum
<archive name>.purged.json` gives. `sha256sum -c SHA256SUMS` checks the archive folder alone: the receipt lies outside
it. After a purge, the archive folder, its receipt and their off-host copies are the only copies of that history
outside the backups: never remove them.

### Moving to a new host

The archive folder belongs to the installation: no backup of the database holds the archives. When PartFlow moves to
another host (§6, Moving to another host), copy the whole archive folder to the new host, keep its owner and mode
0700, and set `PARTFLOW_ARCHIVE_DIR` there before the first archival run. Create the history archive task on the new
host ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.8), and remove it on the old host.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-18 | uncommitted | §6 and §8: disable the history archive task before any restore, because a restored `PURGING` run resumes its purge; §8 and §11: a transient database error leaves a purge `PURGING` for the next start, which reconciles when it ends. |
| 2026-11-18 | uncommitted | §11: the history archive job purges archived Movement history after the off-host copy and a pre-purge backup, then reconciles; the states `PURGING` and `COMPLETED_WITH_SKIPS`, the results `PURGED` and `NOTHING_PURGED`, the purge receipt, the resumed purge, the pre-purge backup and its wait, and returning disk space with `VACUUM FULL`; §8: the incident of a reconcile finding after a purge, during which the archive job works on no run; §3: the `pre-purge` backup kind, its rotation and `--lock-wait-minutes`; §1, §2, §7, §9, §10 and the `pf` table for the purge. |
| 2026-11-17 | `2f12d33` | §11 and the `pf` table: the history archive job (`archive.sh`, `pf archive`), which exports and copies archives and never deletes anything; §1, §2, §6, §8 and §9 for its records, check lines, move, problems and schedule. |
| 2026-11-17 | `4539a2b` | The recovery command refuses a password that breaks the password rules, and the sign-in settings moved to Administration → Security. |
| 2026-11-17 | `01ce427` | §10 and the `pf` table: `pf delete-permanently` deletes Work Orders and Part Numbers entered only for testing, after a preview and a verified backup. |
| 2026-11-17 | `1fc96e1` | Common problems and Sign-in and user accounts: a lost passkey, and a reset that deletes the passkeys too. |
| 2026-11-17 | `aa6287d` | Common problems and Sign-in and user accounts: a lost authenticator app of two-step sign-in, signing the user out everywhere, and a lock after wrong codes. |
| 2026-11-16 | `a4cf9a8` | §7: the reconcile form for a Deployment Admin staging instance is removed with that tool. |
| 2026-11-16 | `5708664` | Before you run any command: the short command `deploy/production/pf`. |
| 2026-11-16 | `c1f042b` | §2, §3, §5, §8, §9: the backup task runs `backup.sh --kind scheduled` every hour; Administration → Backups sets the interval and the hourly, daily and weekly backups kept; the `backup_age` limit follows the interval. |
| 2026-11-14 | `2a24c2a` | §5: the release stops before the write freeze when the new image's `revision` reports `blocked_by_data`. |
| 2026-11-13 | `4a99914` | §2: the successful automatic refreshes of the Production Board are no longer in the `web` log. |
| 2026-11-12 | `de4d99f` | §5 Observe: a Scan Station or Production Board reloads after a 10-second countdown in the notice instead of after 60 s, and every other page once idle for 1 minute instead of 2; §2: an open Production Board adds about 9 MB of `web` log a day instead of 3 MB. |
| 2026-11-12 | `77a2dfb` | §5 Observe: pages open during a release now reload themselves (a Scan Station or Production Board after 60 s, every other page once idle for 2 minutes with nothing unsaved), so nobody visits each computer. |
| 2026-11-12 | `3fb7201` | Added sign-in and user accounts: recovering a forgotten password or login name, renaming a user, removing a user's access, and the recovery when no Administrator is left. |
| 2026-11-10 | `e0e5056` | Rewritten for new operators: a task index and the setup of every session up front, a table of common problems, step-by-step rollback paths 1 and 2 with the checkout switch, the move to another host (moved here from the NAS guide), and shorter reference detail; section numbers and the path 3 scripts are unchanged. |
| 2026-11-09 | `b405c6e` | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-09 | `4271f61` | Added Work Order Demand log searches and the error check to run before recreating `backend`. |
| 2026-10-09 | `03bd90c` | Added monitoring: structured logs with request ids, the `status` command, `check.sh` and the scheduled reconciliation. |
| 2026-10-08 | `92727f6` | Made the rollback path 3 scripts stop at every failed guard and reopen `web` only after `backend` is ready. |
| 2026-10-08 | `a330383` | Added production backups, the isolated restore drill, the pre-release backup inside the write freeze and rollback path 3. |
| 2026-10-08 | `1116b07` | Added the database roles, grants applied by every migration and the repairs for reconcile check (h). |
| 2026-10-08 | `b820e7a` | Added the release flow with `release.sh`, the write freeze and rollback paths 1 and 2. |
| 2026-10-08 | `d7e86a9` | Pointed the procedures at the production Compose stack. |
| 2026-10-07 | `3fb66df` | Added the read-only reconciliation command and its checks. |
| 2026-09-02 | `d2f68de` | First version, with the Vietnamese mirror. |
