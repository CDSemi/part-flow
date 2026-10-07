# PartFlow Operations Runbook

> **Status:** Canonical operational procedure template for Phase 16. Commands
> that depend on future production Compose artifacts must be replaced by their
> final repository-provided names before production use.
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
| Migration output |  |
| Smoke/reconciliation results |  |
| Rollback deadline and observation owner |  |
| Known limitations |  |

## 2. Health and diagnosis

Minimum checks:

```bash
docker compose ps
docker compose logs --since=15m backend frontend db
curl --fail --silent --show-error https://<partflow-host>/api/health
```

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

Example inside an isolated Compose project:

```bash
docker compose exec -T db sh -c \
  'createdb -U "$POSTGRES_USER" partflow_restore_test'
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d partflow_restore_test --exit-on-error --no-owner --no-privileges' \
  < <verified-dump-file>
```

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
  incidents for any findings;
- decide whether writes must be stopped;
- announce the window and rollback decision deadline.

### Execute

1. Record current application and Alembic revisions.
2. Build/pull the target immutable images.
3. Stop or block writes as required.
4. Run the production repository's explicit `alembic upgrade head` job once.
5. Capture migration output and new revision.
6. Start/recreate application services at the target release. On a database
   with no Administrator, complete first-run setup (the setup token is in the
   backend log) before opening access. Then enroll each Scan Station device
   (Administration → Scan Stations → `Devices…`).
7. Check health internally and through HTTPS.
8. Run authorization, SPA-route, `/api`, scan-focus/connectivity, and designated
   write/read-back smoke tests.
9. Run reconciliation (§7) and keep the JSON report. Compare it with the
   pre-release report: only findings absent from it block step 10;
   pre-existing findings stay open incidents under the owner's decision.
10. Reopen writes only when every required check passes.

### Observe

Monitor errors, latency, locks, restarts, disk, and operator feedback through the
defined observation window. Retain the previous release and backup.

## 6. Rollback decision tree

1. **No schema migration occurred:** redeploy the previous immutable application
   release and run smoke checks.
2. **Schema migrated and is backward-compatible:** deploy the previous release
   only if compatibility was explicitly verified before migration.
3. **Schema migrated and is not backward-compatible, or compatibility is
   unknown:** stop writes; restore the pre-migration database into a clean
   instance and deploy the matching previous application release.
4. **New production writes occurred after migration:** do not blindly restore
   over them. Escalate; preserve both the current database and pre-release
   backup, determine a forward fix or audited data-recovery plan, and keep the
   application write-blocked.

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
- **Production:** the invocation arrives with the Phase 16 production artifacts.
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
| (f) | demand `allocated_quantity` and Work Order `completed_at` reconcile with active allocation rows; |
| (g) | no retained Movement references a purged row; |
| (h) | no append-only table was mutated outside an approved archival/purge path; |
| (i) | Hot list entries are active demand (the `DEPLOYMENT.md` §5 query); |
| (j) | canonical identity under the running interpreter and database: canonical PNs, case-insensitive Worker badges, the Asset Tag prefix rule, every canonical-form CHECK re-evaluated under the running database collation and ctype, the collation version, and index-independent duplicate probes of the identity keys (the platform-upgrade identity check). |

(g) and (h) report `not_applicable` until Movement-history archival and
database-role hardening exist; they are neutral for the exit code.

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

### Database or storage pressure

- block new writes before disk is exhausted;
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
| Monthly | Patch in staging then production; review users/roles, firewall rules, secrets, and runbook contacts |
| Quarterly or after material schema change | Full isolated restore drill, measured RPO/RTO exercise, and reconciliation review |
| Before every release | Fresh verified backup, migration review, rollback decision, and smoke-test plan |

The organization must set actual RPO, RTO, retention, and owners. Examples in
this runbook are procedures, not service-level commitments.

