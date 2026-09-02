# Deploying PartFlow on a Linux VPS

> **Status:** Target production path for Phase 16. The current development
> Compose stack is not a production package.
>
> **Language:** English is the source of truth. [Tiếng Việt](./VPS.vi.md).

## 1. When to choose a VPS

Choose a VPS when PartFlow needs predictable Docker/Compose behavior,
independent resource control, secure remote access, provider snapshots plus
application-level backups, or a clean upgrade path from Synology. A VPS keeps
the existing React/FastAPI/PostgreSQL architecture intact.

## 2. Required Phase 16 artifacts

Do not deploy production from `compose.yaml`. The release must provide:

- production frontend and backend Dockerfiles/images;
- production Compose configuration;
- reverse proxy configuration and certificate procedure;
- explicit migration command/job;
- secret/configuration inventory;
- backup and restore automation;
- health and reconciliation commands;
- logging/monitoring configuration;
- release and rollback procedure tied to immutable versions.

## 3. Host baseline

- supported 64-bit Linux distribution with current security updates;
- dedicated non-root deployment account with SSH keys;
- Docker Engine and Compose plugin from a supported source;
- host firewall: SSH from administrative sources, HTTP/HTTPS as approved, all
  other inbound ports denied;
- automatic security updates or a documented patch window;
- correct NTP/timezone policy;
- separate persistent storage for PostgreSQL and local backup staging;
- encrypted provider/off-site backup destination;
- resource and disk monitoring;
- no unrelated experimental workloads on the production host.

Never publish PostgreSQL to the internet. Prefer a private provider network for
remote backup/database services when available.

## 4. DNS and TLS

Use a dedicated hostname such as `partflow.company.example`. Point DNS only
after the private smoke test succeeds. The reverse proxy terminates TLS and
routes `/` to the static frontend and `/api` to FastAPI. Confirm direct loading
of SPA routes and `/api/health` through HTTPS.

If PartFlow is internal-only, restrict access through firewall/VPN/private DNS.
Public reachability still requires full application authentication and
authorization; a secret URL is not a control.

## 5. Filesystem layout

Example:

```text
/srv/partflow/
  releases/<immutable-release>/
  current -> releases/<immutable-release>/
  env/production.env
  data/
  backups/database/
  manifests/
```

The deployment account owns release files. Secrets are readable only by the
required account/service. PostgreSQL data is never inside a Git checkout.

## 6. Initial production deployment

1. Complete all gates in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Provision and harden the host.
3. Install the exact release files/images; record their digest/commit.
4. Create production secrets and least-privilege database roles.
5. Start PostgreSQL privately.
6. Restore approved seed data or create an empty database.
7. Run `alembic upgrade head` once from the release backend image.
8. Start backend, frontend, and reverse proxy.
9. Run the runbook smoke and reconciliation checks through HTTPS.
10. Enable monitoring and backup schedules, then run a backup immediately.
11. Perform and time an isolated restore before pilot data is accepted.
12. Open only the approved network sources and begin the controlled pilot.

## 7. Releases and rollback

Use immutable directories/images. Build/pull the new release before stopping the
old one. Back up before migration. Never deploy directly from a mutable `main`
checkout.

Application rollback is allowed only when the previous code is compatible with
the migrated schema. If not, restore the pre-migration database and matching
application release together. Alembic downgrade is not a generic rollback:
PartFlow migrations may protect immutable history by refusing destructive
downgrades.

Follow [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 8. Backup strategy

Use PostgreSQL logical dumps as the portable baseline and optionally add
provider volume snapshots as a second layer. Snapshots never replace tested
logical restore.

- scheduled custom-format `pg_dump`;
- checksum and manifest containing release commit and Alembic revision;
- encryption in transit and at rest;
- off-VPS copy with retention and failure alert;
- periodic restore to an isolated database;
- documented RPO/RTO measured from actual runs.

## 9. Moving from Synology

Use the dump/restore cutover in `SYNOLOGY_NAS.md` §10. Keep source and target at
the same release, enforce a write freeze, verify checksums and reconciliation,
then switch DNS. Never run two writable production instances against divergent
databases.

