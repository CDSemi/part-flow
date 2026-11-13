# PartFlow Operations Runbook

> **Language:** English is the source of truth. [Tiếng Việt](./OPERATIONS_RUNBOOK.vi.md).

This runbook holds the procedures for running PartFlow production once it is installed: checking that it is healthy,
taking and testing backups, installing a new release, rolling back, running the reconciliation, handling problems and
the routine schedule. It is written for the operator, who today is also the owner. The installation itself is in
[`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) (or [`VPS.md`](./VPS.md)), and every setting and limit of the stack is
described in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1.

## Find your task

| I want to … | Section |
| --- | --- |
| Check that PartFlow is healthy, or find the cause of an error | §2 |
| Take a backup by hand, verify one, or understand the backup folder | §3 |
| Prove that a backup can be restored | §4 |
| Install a new release | §5 |
| Undo a release | §6 |
| Restore after a host failure, or move PartFlow to another host | §6 (New-instance restore, Moving to another host) |
| Run or read the reconciliation | §7 |
| Solve a problem: common symptoms and incidents | §8 |
| Know what to do daily, weekly and monthly; change a password; remove old backups | §9 |
| Reset a forgotten password or login name, rename a user, or remove a user's access | §8 (Sign-in and user accounts) |
| Find what a past release did | §1 |

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

Conventions: a one-shot job that reads or writes the backup folder runs with `--user "$(id -u):$(id -g)"`, because
that folder has mode 0700. `<partflow-host>` is the host name people type in the browser, `<backup-dir>` is the value
of `PARTFLOW_BACKUP_DIR`, `<secrets-dir>` the value of `PARTFLOW_SECRETS_DIR`, `<records-dir>` the records directory
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
| `disk_archive` | the archive folder has less than 15 % free; `SKIP` while no archive folder is given (`--archive-dir`) |
| `database` | the `status` report could not run, did not answer within 120 s, or reported a database error |
| `schema` | `status` reports the schema as not ready |
| `backup_age` | no published backup is found, the backup folder cannot be read, or the newest backup of any kind completed more than 26 hours ago (`--max-backup-age-hours`) |
| `archival_proposal` | always `SKIP`: the archival of Movement history is not implemented yet |

- The last four lines come from one read-only `status` run. While a release holds its lock, or `release.sh` holds the
  backup lock, they are `SKIP`. When such a lock is older than 4 hours, the run reports `FAIL database` and names the
  lock, because a killed `release.sh` leaves it behind (§3, Backup lock and release lock).
- Exit codes: 0 nothing to report, 1 at least one `FAIL`, 2 the check could not run; both 1 and 2 reach the
  scheduler's failure notification. An unchanged set of failures is reported again only after 6 hours
  (`--renotify-hours`); the output then says `already reported`.
- `check.sh` keeps its state in `~/partflow-monitoring` (mode 0700), including `last-check.txt` (the lines of the last
  run) and `growth.tsv` (the database size once a day).
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
that is about 9 days of backend log and 18 days of `web` log; every open Production Board adds about 9 MB of `web`
log a day, so with one board open the `web` log covers about 4 days, and with several boards only a day or two.
**Copy what an investigation needs as soon as it starts.**

### Database permission errors

The backend connects as the database role `partflow_app`. A `permission denied` error (SQLSTATE 42501) in its log
means that a code path tried to change protected history or lacks a grant. It is an incident (§8), never a reason to
grant more. Run `reconcile --check h` (§7) before anything else.

## 3. Backups

### What a backup is

`deploy/production/backup.sh` takes a logical backup of the PartFlow database while `db` runs. It runs from the root
of a release checkout: the running release's for scheduled and manual backups, the new release's when `release.sh`
calls it. One backup is one folder `<backup-dir>/<NAME>/`:

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-daily
                                         and 20261008T140000Z-pre-release-v1.0.0-rc.2
```

- `kind` is `daily` (the scheduled backup), `manual` (taken by hand, with a reason) or `pre-release` (taken by
  `release.sh`, with the target release tag as `label`).
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

On success, the standard output is exactly one line, `BACKUP <NAME> <absolute path>`; progress lines such as
`backup: dump ok (1234 ms)` go to standard error. The scheduled daily backup is
`deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8`
([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.2). `release.sh` takes the pre-release backup itself; its manual form, for
a release by hand, is
`deploy/production/backup.sh --kind pre-release --label <target tag> --tools-release <target tag> --operator "<your name>"`.

| Option | Meaning |
| --- | --- |
| `--kind daily\|manual\|pre-release`, `--operator NAME` | Required. |
| `--reason TEXT` | Required for `manual`. |
| `--label TAG` | The target release tag: required for `pre-release`, refused otherwise. |
| `--tools-release TAG` | The release whose `backup-tools` image writes and verifies the manifest (default: `PARTFLOW_RELEASE`). |
| `--keep-daily N --keep-weekly N`, or `--no-rotate` | `daily` only, and one of the two is required: the retention (below). There is no default. |
| `--reserve-mib N` | Free space kept beyond twice the newest dump (default 1024). |

Text values are 1 to 500 characters without control characters, `"` or `\`. Before writing, `backup.sh` requires free
space of twice the newest dump (or the database size when there is no dump yet) plus the reserve.

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Completed and verified. | Nothing. |
| 1 | Refused before writing: `backup_running`, `backup_lock_stale`, `insufficient_space` or `name_exists`. | Read the reason; nothing was written. |
| 2 | Could not run: options, tools, configuration, `db` not running, or the backup lock cannot be created. | Fix the cause and run it again. |
| 3 | Failed: no backup was published, or the published backup failed verification. | Never use a backup that the message names; remove it after review. |
| 4 | The backup is complete and verified, but the rotation failed or found a daily backup that fails verification. | Read the rotation report on standard error (Retention, below). |

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

### Retention, the off-host copy and alerts

A `daily` backup ends with the rotation of the daily backups. To preview it without deleting anything:

```sh
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-daily 14 --keep-weekly 8 --dry-run
```

- It keeps the newest `--keep-daily` verified daily backups, plus the newest daily backup of each of the newest
  `--keep-weekly` calendar weeks (ISO weeks in UTC; the weeks overlap the daily window).
- It verifies every candidate first. A daily backup that fails verification is not counted and **never deleted**:
  `backup.sh` exits with 4 and you review it (§9).
- **Pre-release and manual backups are never rotated**: you remove them after review (§9). No tool ever rotates or
  deletes an archive folder.
- Leftovers in `.partial/` older than 24 hours are removed.
- 14 and 8 are examples; the owner chooses the real values in the scheduled command. They are required options,
  never built-in defaults.

The platform's backup tool encrypts the backup folder and copies it off the host, never a hand-made copy: Hyper Backup
with client-side encryption on the NAS ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.3), restic to object storage on a
VPS ([`VPS.md`](./VPS.md) §8).

Alerts, once the schedules are set up: a failed scheduled backup (any non-zero exit) raises the scheduler's failure
notification; a failed off-host copy raises the backup tool's own notification; and the `check.sh` check
`backup_age` fails when the newest backup is older than 26 hours (§2). With a daily backup, at most 24 hours of work
can be lost (the recovery point objective, RPO). Until the daily backup is scheduled, the only backups are the ones
`release.sh` takes before each release and any manual backup.

### Backup lock and release lock

Backups never run at the same time. The folder `<backup-dir>/.backup.lock` is the lock; its file `owner` holds
`host`, `pid`, `started_at`, `by` (`backup.sh` or `release.sh`) and the backup `name` or the `release`. A second run is
refused, never queued: `backup_running` means that a backup, or a release holding the lock, is in progress, so wait.
`release.sh` holds the lock from the write freeze (or from its backup) until the new `backend` runs. **Keep the daily
backup away from release times:** a running backup stops a release before the freeze with nothing changed, and a
daily backup that starts during a release is refused. A lock that cannot be created at all (a full, read-only or
inode-exhausted backup folder) is reported as could-not-run (exit 2), never as `backup_running`.

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
- Choose a time when no Work Order import runs and no backup runs (the daily backup at 02:00 on the NAS).
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
   release and commit.
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

### Path 3 procedure

Path 3 applies only when the release record shows `writes_reopened_at: null` (the release stopped frozen) or the owner
records that no production write happened after the migration; otherwise it is path 4. Restoring into production
needs the **owner's approval, recorded in `rollback.json` before `backend` starts** on the restored database; without
it the procedure stops after `reconcile`, with PartFlow write-blocked.

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
The development form is in the root [`README.md`](../../README.md); for a staging instance of the Deployment Admin tool
`pf`, see [`SYNOLOGY_ADMIN.md`](./SYNOLOGY_ADMIN.md) §14.

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
| (a) Current positions replay from Movement history | The current positions equal a replay of the Movement history that was not reversed. |
| (b) Quantity Flow history and conservation | Every active or closed flow has a valid conservation history, plus the cross-row rules of [`SLICE1_DATA_MODEL.md`](../SLICE1_DATA_MODEL.md) §17 except the audit-row rule. |
| (c) Per-PN quantity balance | The quantity introduced per PN equals its active, stocked, scrapped and reversed outcomes. |
| (d) Machine assigned quantities | The quantity assigned to each Machine equals the flows on it. |
| (e) Release evidence and Work Order status | Each demand's `released_quantity` follows from its `RECEIVED` evidence. |
| (f) Allocations and Work Order completion | Each demand's `allocated_quantity` and each Work Order's `completed_at` follow from the active allocations; an authorized allocation beyond the demand (`exceeds_demand`) is not a finding. |
| (g) Retained Movements reference no purged row | No kept Movement points to a purged row. |
| (h) Append-only guards and database-role privileges intact | The guard triggers and functions are present, enabled and unchanged; `partflow_app` and `partflow_maintenance` hold exactly their grants (no `UPDATE`, `DELETE` or `TRUNCATE` on append-only history), keep their safe attributes and have no membership; `PUBLIC` and the default privileges hold nothing; and `session_replication_role` is not `replica`. It cannot see a change by a superuser who disabled a trigger and restored it afterwards (an accepted limit). |
| (i) Hot list entries are active demand | The Hot list query of [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5 returns no row. |
| (j) Canonical identity under the running interpreter and database | Canonical PNs, case-insensitive Worker badges, the Asset Tag prefix rule and every canonical-form constraint hold under the running database's sorting rules, the collation version is unchanged, and no identity key is duplicated. |

Check (g) reports `not_applicable` until the archival of Movement history exists, and check (h) reports
`not_applicable` only on a database without the PartFlow roles (development, tests, staging); both are neutral for the
exit code. In production, a missing role is a finding.

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

`deploy/production/scheduled-reconcile.sh` is the daily form, recommended at 04:00, after the daily backup, from the
running release's checkout, as the operating account ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.5).

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
| `check.sh`: `FAIL backup_age` | The daily backup did not run or failed: check its task and its emails, then run a manual backup (§3). |
| `check.sh`: `FAIL disk_...` | Database or storage pressure, below. |
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
  1 means refused with nothing changed (an unknown login name, two different passwords, a user who never had a
  password, or no Administrator at all); 2 means the outcome is unknown or the database could not be reached: run it
  again, because it sets the password again either way. It works only while an Administrator exists and never
  creates one.

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

### Database or storage pressure

- Block new writes before the disk is full (the write freeze, §5), and keep the logs and figures.
- The storage alert is a `check.sh` line `disk_*` below 15 % free: `disk_data` for the database volume, `disk_backup`
  for the backup folder, `disk_docker` for the Docker storage (images and container logs), `disk_archive` for the
  archive folder.
- **Do not delete PostgreSQL files, volumes, Movement rows or backups to make room.**
- Space rules: a backup needs twice the newest dump plus a reserve free in the backup folder; a path 3 restore needs
  the database size plus twice the dump plus 1 GiB on the database volume (§6); a stale backup lock blocks backups
  and releases until it is removed (§3).
- Expand the storage. The archival and purge of Movement history are planned but not implemented
  ([`../IMPLEMENTATION_ROADMAP.md`](../IMPLEMENTATION_ROADMAP.md), Phase 16).
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
| Every 15 minutes (automatic) | `check.sh`: health, restarts, disks, certificate, backup age and backend errors (§2). An unchanged failure is repeated every 6 hours. |
| Daily | The scheduled backup at 02:00 and the scheduled reconciliation at 04:00 run by themselves. Read every failure email, including those of the off-host copy; exit 4 of a backup includes a daily backup that fails verification. |
| Weekly | Review the database growth (`~/partflow-monitoring/growth.tsv`) and `docker stats --no-stream`. Review failed sign-ins and refused permissions: `$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'` (`refusal.type` and `refusal.code` tell a refused or locked sign-in, a permission refusal, `station_device_required`, `station_device_mismatch` and `csrf_rejected` apart). Check pending security updates of the host. |
| Monthly | Remove old reconciliation reports after review. Remove pre-release and manual backups whose observation time has ended, and daily backups that the rotation reported invalid, after review (below). Install updates, on a test system first where possible. Review users and roles, firewall rules and secrets. Run `reconcile --check h`. |
| Quarterly, and after a large schema change | The restore drill (§4); compare its `restore_to_ready` and `total` times with how long the factory can work without PartFlow. |
| Before a change of the PostgreSQL image or of the host's glibc | `restore-test.sh --backup <latest> --db-image <candidate>` (§4, §7). |
| Before every release | Review the migrations, the rollback decision and the checks (§5); `release.sh` takes the backup. |

**Removing old backups and reports.** No tool deletes pre-release backups, manual backups, daily backups that fail
verification, or reconciliation reports. After review, remove each one by name; never remove the newest verified
daily backup or anything in an archive folder:

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
  `partflow_maintenance_password`, replace that file and run only the `db-roles` command: no service uses that role
  yet, so no freeze is needed.
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

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-12 | uncommitted | §5 Observe: a Scan Station or Production Board reloads after a 10-second countdown in the notice instead of after 60 s, and every other page once idle for 1 minute instead of 2; §2: an open Production Board adds about 9 MB of `web` log a day instead of 3 MB. |
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
