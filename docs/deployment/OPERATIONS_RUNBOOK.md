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
- decide whether writes must be stopped;
- announce the window and rollback decision deadline.

### Execute

1. Record current application and Alembic revisions.
2. Build/pull the target immutable images.
3. Stop or block writes as required.
4. Run the production repository's explicit `alembic upgrade head` job once.
5. Capture migration output and new revision.
6. Start/recreate application services at the target release.
7. Check health internally and through HTTPS.
8. Run authorization, SPA-route, `/api`, scan-focus/connectivity, and designated
   write/read-back smoke tests.
9. Run reconciliation.
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

Phase 16 must provide read-only commands that at minimum verify:

- current-position projections replay from non-reversed Movement history;
- every active/closed flow has a valid conservation history;
- per-PN introduced quantity reconciles with active, stocked, scrapped, and
  reversed outcomes under the canonical rules;
- Machine assigned quantities reconcile with flows currently on each Machine;
- demand `released_quantity` derives from `RECEIVED` evidence;
- demand `allocated_quantity` and Work Order `completed_at` reconcile with
  active allocation rows;
- no retained Movement references a purged row;
- no append-only table was mutated outside an approved archival/purge path.

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

