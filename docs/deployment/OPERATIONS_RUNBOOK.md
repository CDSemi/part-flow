# PartFlow Operations Runbook

> **Status:** Canonical operational procedure template for Phase 16. The
> production Compose file exists (P16-S2): `PF` below is
> `docker compose -f compose.production.yaml --env-file .env.production`, run
> from the release checkout ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1). The
> release, migration, write-freeze and rollback commands (paths 1 and 2) are
> real (P16-S3: `deploy/production/release.sh`, `smoke.sh`, `migrate`,
> `revision`); the backup, restore and rollback path 3 commands (P16-S5) and the
> monitoring commands (P16-S6) are still placeholders and must be replaced by
> their final repository-provided names before production use.
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
(row 5), `started_at`, `finished_at` (row 6), `backup` (row 7; `verified` is
`false` until P16-S5), `migration` (the `migrate.json` and `migrate.log`
files; row 8; `migrate.json` carries the `grants` result, and the `provision-roles` and `apply-grants` JSON reports of a manual run are kept beside it), `reconcile` and `smoke` (rows 9), `rollback_deadline` and
`observation_owner` (row 10), `known_limitations` (row 11); `outcome`,
`writes_reopened_at` and `refrozen` state how the run ended. A manual
operation (for example a rollback) appends to the same directory with the same
fields.

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

`/api/health` is readiness: it reports `release`, `commit`, `schema`
(`current`, `accepted`, `mismatch` or `unknown`), `expected_revision`,
`database_revision` and `accepted_revision`, and answers 503 on a schema
mismatch or an unreachable database. `/api/health/live` is liveness only (the
container health checks use it): it stays healthy while readiness is 503 during
a schema mismatch. `revision` prints the same facts from a one-off container
and works while `backend` is stopped.

In the production stack `web`'s access log is the request log (client address,
method, path without query string, status, bytes, duration, user agent); it
never contains query strings, cookies or PartFlow headers. A 502 or 504 JSON
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

Create a custom-format dump:

```bash
mkdir -p backups/database manifests
backup_file="backups/database/partflow-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$backup_file"
test -s "$backup_file"
pg_restore --list "$backup_file" > "$backup_file.list"
sha256sum "$backup_file" "$backup_file.list"
```

If `pg_restore` is not installed on the host, run the list check in a matching
PostgreSQL client container. Store with the dump:

- UTC timestamp;
- environment;
- Git commit/image digests;
- Alembic current revision;
- PostgreSQL major version;
- dump/list checksums;
- operator and backup reason.

Dumps carry no grants by design (`--no-privileges`): the database roles and their privileges are re-derived after a restore (§4).

Encrypt and copy the bundle off-host. Alert when a scheduled backup is missing,
empty, too old, or fails off-site replication.

## 4. Restore test — never overwrite first

Restore into an isolated database or isolated stack, never directly over the
only production database:

1. verify dump and manifest checksums;
2. provision the same PostgreSQL major version or a documented compatible
   target;
3. create an empty restore-test database;
4. restore with `pg_restore --exit-on-error --no-owner --no-privileges`;
5. start the matching application release against that database;
6. verify Alembic revision;
7. run health, representative read models, quantity/movement/allocation
   reconciliation, and designated smoke tests;
8. record restore duration and result;
9. destroy the isolated restore copy only after evidence is retained.

In a restore into a new cluster, provision the database roles first (`provision-roles`, with throwaway passwords in a restore drill), and run `apply-grants` against the restored database before starting the application (step 5): a dump contains no grants, so a restored database has none until `apply-grants` re-derives them, and the application role cannot work without them. Reconcile check (h) must pass before the application starts.

Example inside an isolated Compose project:

```bash
docker compose exec -T db sh -c \
  'createdb -U "$POSTGRES_USER" partflow_restore_test'
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d partflow_restore_test --exit-on-error --no-owner --no-privileges' \
  < <verified-dump-file>
```

With the production stack the roles and grants of the restore database are created with the commands of `DEPLOYMENT.md` §3.1 (`$PF --profile ops run --rm -T db-roles`, then `… db-roles apply-grants`), with `DATABASE_NAME` pointed at the restore database.

Use explicit restore-test names. Never substitute the production database name
in a rehearsal command.

## 5. Release and migration

### Before maintenance

- approve exact release revision and scope;
- confirm CI/quality results for that revision;
- review every migration and its downgrade/recovery behavior;
- estimate lock/time/disk impact using staging data;
- verify off-site backup health and make a fresh pre-release dump;
- verify the previous release remains available;
- run reconciliation (§7) on the current release, keep the report, and open
  incidents for any findings; in the production stack run it, and any recovery
  CLI, with the **current** tag, before the candidate tag is written to
  `.env.production` (the candidate lives only in the shell until the switch);
- decide whether writes must be stopped (a pending migration always needs the
  write freeze below);
- announce the window and rollback decision deadline, and ask stations to
  finish their open dialogs;
- take the pre-release dump as late as possible and record its time: until
  P16-S5 it is taken before the freeze, so writes made between the dump and the
  freeze are **not** in it; name it with `--pre-release-backup`.

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
deploy/production/release.sh --release <new-tag> --operator "<name>" --approver "<name>" \
    --pre-release-backup "<dump reference>"
```

(`--no-backup-reason "<text>"` instead of the dump reference when there is none,
for example a rehearsal; `deploy/production/release.sh --help` prints every
option.) It runs, recording each step: preflight (tools, the environment file,
the tag form, clean build inputs); the current revision and the pre-release
reconcile with the running release; the candidate build (an existing tag is
never rebuilt, and is reused only when both images were built from this commit
as this release); the candidate's check (j) and revision (which must report this
release and commit); the write freeze when a
migration is pending; `migrate` (which also applies the grants); the post-release reconcile; the `backend`
switch while `web` still serves the previous bundle (writes stay refused, every
loaded page sends the previous release and gets 409), the health wait for the
new release and a `current` schema; the `web` switch, which reopens writes; and
`smoke.sh`. A failed check after the switch stops `backend` again. Its preflight also refuses, with nothing changed, while `partflow_app_password`, `partflow_maintenance_password` or `postgres_password` is missing, empty or not a regular file.

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
3. Freeze when a migration is pending: `$PF stop backend`, then confirm
   `$PF ps --status running -q backend` prints nothing.
4. `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T migrate (--pre-release-backup REF | --no-backup-reason TEXT)`;
   capture its JSON output (the `grants` field reports the applied grants) and the new revision.
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
defined observation window. Retain the previous release and backup. Pages that
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
   unknown:** stop writes; restore the pre-migration database into a clean
   instance and deploy the matching previous application release. The restore
   procedure is a P16-S5 placeholder; a restore of the pre-release dump never
   discards writes made after it without the path 4 escalation.
4. **New production writes occurred after migration:** do not blindly restore
   over them. Escalate; preserve both the current database and pre-release
   backup, determine a forward fix or audited data-recovery plan, and keep the
   application write-blocked.

Recovery CLIs run with the release that matches the database.

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
  for a glibc or PostgreSQL-image change, run `--check j` on a restore onto the
  candidate server (restore drill) or right after an in-place upgrade before
  writes reopen. The glibc/PostgreSQL-image half is **pending until the restore
  drill exists**. The owner decides on re-canonicalization before any upgrade.
- For a Work Order reported `not_completed_but_fully_allocated`, `expected` is
  the replay value, not a repair proposal; the owner picks the done date in the
  incident.
- A non-zero exit is an incident (§8). Never edit history or projections
  directly; the owner decides each repair.

Reconciliation is read-only by default. A mismatch creates an incident; it does
not trigger an automatic repair.

## 8. Incident response

### Suspected duplicate, lost, or uncertain write

- stop the affected workflow if quantity integrity may be at risk;
- preserve request time, station, user/worker, PN, flow, and
  `device_event_id`;
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
- do not delete PostgreSQL files, volumes, Movement rows, or backups ad hoc;
- expand storage or follow the Phase 16 verified archive/purge maintenance path;
- run reconciliation before reopening writes.

### Host failure

- prevent split-brain: confirm the failed instance cannot accept writes;
- provision the approved recovery host;
- restore the latest verified backup and matching release;
- run reconciliation and smoke tests;
- document data-loss window against the approved RPO;
- redirect clients only after approval.

## 9. Routine schedule

| Frequency | Tasks |
| --- | --- |
| Continuous | Health, restart, disk, certificate, backup-age, and error alerts |
| Daily | Review backup success and off-site replication; review critical errors |
| Weekly | Review capacity trend, database growth, failed logins/authorization events, and pending security updates |
| Monthly | Patch in staging then production; review users/roles, firewall rules, secrets, and runbook contacts; review database roles with `reconcile --check h` |
| On role-password rotation | A short write freeze: replace the role file, `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, `$PF up -d --force-recreate --no-deps backend`, then check health. A plain `up -d backend` does not pick up the new password (the container is not recreated), and running `db-roles` while the backend serves makes its new connections fail. Owner password: `ALTER ROLE … PASSWORD` inside `db` first, then replace `postgres_password` (no service restart: only the one-shot `migrate` and `db-roles` use it) |
| Quarterly or after material schema change | Full isolated restore drill, measured RPO/RTO exercise, and reconciliation review |
| Before every release | Fresh verified backup, migration review, rollback decision, and smoke-test plan |

The organization must set actual RPO, RTO, retention, and owners. Examples in
this runbook are procedures, not service-level commitments.

