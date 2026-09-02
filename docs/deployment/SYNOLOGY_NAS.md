# Deploying PartFlow on a Synology NAS

> **Status:** The current repository supports a restricted internal staging
> deployment only. The production path in this document becomes actionable when
> the Phase 14 and Phase 16 gates in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) are
> complete.
>
> **Language:** English is the source of truth. [Tiếng Việt](../vi/deployment/SYNOLOGY_NAS.md).

## 1. Intended use

Use the company's Synology NAS now to let a controlled internal group exercise
the real Phase 3.5–10 surfaces and verify shop-floor ergonomics. Use synthetic
or disposable data. Do not represent this staging instance as production and do
not expose it to the internet.

After Phase 14/16, the same NAS may host a pilot or small production instance if
it passes the hardware, operations, backup, and recovery gates below.

## 2. Preflight checklist

Record the result of every item before installation:

- exact Synology model, CPU architecture, installed RAM, DSM version, storage
  pool/volume, filesystem, free space, and RAID health;
- Container Manager is offered for that model and can create multi-container
  Projects from a Compose file;
- the NAS CPU architecture is supported by every selected PartFlow/PostgreSQL
  image;
- enough sustained memory and CPU remain after existing NAS workloads;
- the NAS has a reserved LAN address and correct DNS/NTP;
- the project and backup directories live on a protected volume;
- DSM firewall rules and the company network can restrict the service to the
  intended VLAN/subnets;
- UPS behavior and automatic safe shutdown are configured and tested;
- at least one encrypted backup destination is outside this NAS;
- an administrator can use DSM and SSH during installation and recovery.

If Container Manager is unavailable, do not install unsupported Docker packages
or weaken DSM. Use a Linux VM/VPS instead.

## 3. Directory plan

Create one dedicated shared folder. Do not assume every NAS uses `volume1`; use
the actual volume selected by the administrator. Example:

```text
<NAS_VOLUME>/docker/partflow/
  repo/                 checked-out release
  backups/
    database/
    manifests/
  restore-tests/
```

Permissions:

- only the deployment administrator and the account used by Container Manager
  need write access;
- ordinary DSM users do not need filesystem access;
- `.env`, database dumps, and manifests must not be placed in a web-served
  shared folder;
- do not grant broad `Everyone` read/write permission.

## 4. Restricted staging deployment now

### 4.1 Obtain a pinned revision

Enable SSH temporarily if company policy permits, sign in with a named admin
account, and clone the repository into `repo/`. Check out an explicit commit or
tag; do not deploy a moving branch without recording the resolved commit.

```bash
git clone https://github.com/CDSemi/part-flow.git repo
cd repo
git fetch --tags --prune
git checkout <approved-commit-or-tag>
git rev-parse HEAD
```

If Git is unavailable on the NAS, download an archive for the approved commit
on a trusted workstation, verify it, and extract it into `repo/`.

### 4.2 Configure staging secrets

Copy `.env.example` to `.env` and replace every credential. The real `.env`
stays uncommitted.

```bash
cp .env.example .env
chmod 600 .env
```

Requirements:

- generate a unique long PostgreSQL password;
- use a staging-only database and credentials;
- never reuse DSM, GitHub, production, or personal passwords;
- keep a protected recovery copy in the company's secret manager/password
  vault.

The current `compose.yaml` derives the backend container's `DATABASE_URL` from
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`, and passes
`SITE_TIMEZONE` through (default `UTC`). Set `SITE_TIMEZONE` to the factory's
IANA zone (for example `America/Los_Angeles`) before the first start: the
backend refuses an unknown zone name, and the completed history's done dates
and due outcomes are judged on this calendar.

### 4.3 Limit published ports

The current Compose file publishes PostgreSQL `5432`, backend `8000`, and
frontend `5173`. Only the frontend entry point is required by ordinary staging
clients because Vite proxies `/api` to the backend.

Before starting:

1. remove the `db` and `backend` `ports` entries in the NAS deployment copy of
   the Compose file;
2. publish frontend `5173` only on the reserved LAN address, or bind it to
   `127.0.0.1` when DSM Reverse Proxy is used;
3. keep the Compose service network private;
4. add DSM firewall rules restricting the chosen frontend/reverse-proxy port to
   the intended company subnets.

Do not commit NAS-specific port or secret changes back to the repository.

### 4.4 Create the Container Manager Project

In DSM:

1. Install/open **Container Manager**.
2. Open **Project** → **Create**.
3. Name the project `partflow-staging`.
4. Select the `repo/` project path and the prepared Compose file.
5. Review the rendered services, mounts, networks, ports, and environment
   values before building.
6. Build and start the Project.

The equivalent SSH command, when Docker Compose access is permitted, is:

```bash
docker compose up -d --build
docker compose ps
```

### 4.5 Apply migrations

Wait for PostgreSQL and backend health, then run the repository's canonical
Alembic upgrade command once:

```bash
docker compose exec backend uv run alembic current
docker compose exec backend uv run alembic upgrade head
docker compose exec backend uv run alembic current
```

Capture the output in the deployment record. Never run `alembic downgrade` on
staging data you need to preserve without first reviewing the specific
migrations and creating a verified backup.

### 4.6 Smoke test

From the NAS:

```bash
curl --fail --silent --show-error http://127.0.0.1:5173/api/health
docker compose ps
docker compose logs --tail=200 backend frontend db
```

From an allowed workstation:

- open the frontend URL;
- verify the connected indicator and `/api/health`;
- verify the intended real views load and explicitly note views still pending
  or unavailable;
- use designated test records to exercise create, release, transfer, Machine,
  correction, stocking, and allocation behaviors in the implemented scope;
- verify a server-confirmed success and refreshed read model;
- stop the backend briefly and verify production writes become blocked, then
  restore it and verify focus/readiness recovery;
- confirm a client outside the allowed subnet cannot connect.

### 4.7 Staging restrictions

- no real production quantities or personal employee data;
- no internet/NAT port forwarding, QuickConnect publication, or public tunnel;
- named testers only;
- manual observation during test sessions;
- regular disposable backups so migration/update rehearsal can be repeated;
- clearly label the URL and UI communication as staging.

## 5. DSM Reverse Proxy for internal HTTPS

For an internal DNS name, create a DSM reverse-proxy rule whose source is HTTPS
and whose destination is the local frontend service. Route the entire origin to
the frontend; the frontend's current Vite proxy forwards `/api` to the backend.

Controls:

- use a certificate trusted by company workstations;
- preserve the original host and forwarding headers;
- allow only the intended LAN/VPN sources in DSM firewall and upstream network
  rules;
- do not expose DSM administration through the PartFlow hostname;
- test deep application routes directly, not only `/`;
- test `/api/health` through the public-facing internal URL.

This improves transport protection but does not make the development servers or
unauthenticated application production-ready.

## 6. Backup staging data

Create a logical PostgreSQL dump from inside the database container. The
variables below expand inside the container, not in the DSM shell:

```bash
mkdir -p ../backups/database
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > ../backups/database/partflow-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Record a manifest beside each dump:

```bash
git rev-parse HEAD
docker compose exec -T backend uv run alembic current
sha256sum ../backups/database/partflow-*.dump
```

Copy the dump and manifest to an encrypted off-NAS destination. A snapshot of a
live PostgreSQL volume is not the only database backup unless a documented,
tested database-consistent snapshot procedure is used.

Follow the restore-test procedure in
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) before trusting the backup.

## 7. Staging update procedure

1. Announce a staging maintenance window.
2. Record the current Git commit and Alembic revision.
3. Create and verify a fresh database dump.
4. Fetch and check out the approved target commit.
5. Review `.env`, Compose, Dockerfile, dependency-lock, and migration diffs.
6. Build the target images.
7. Run `alembic upgrade head` once.
8. Recreate/start services.
9. Run the complete smoke test and reconciliation checks.
10. Retain the old commit and backup through the observation window.

Never use an unattended “latest/main” auto-updater for a database-backed factory
system.

## 8. Production conversion after Phase 16

Do not convert by merely changing the URL. Replace the development stack with
the Phase 16 production artifacts and verify all production gates:

- immutable production frontend and backend images;
- production Compose file with no source bind mounts, no reload/dev server, no
  published database port, and explicit restart/resource/logging policies;
- private backend/database networks and one reverse-proxy entry point;
- Phase 14 authentication/authorization;
- secret handling and separate least-privilege database roles;
- scheduled logical backups, encrypted off-NAS replication, retention alerts,
  and successful restore drill;
- monitoring for health, logs, disk, backup age, restart count, and database
  growth;
- release, migration, rollback, reconciliation, and incident runbooks tested by
  the actual administrators;
- approved RPO/RTO and pilot scope.

## 9. Capacity and reliability

Do not treat a generic RAM/CPU number as proof of readiness. Measure during a
realistic staging test:

- idle and peak CPU/RAM for all services and existing NAS workloads;
- PostgreSQL data and index growth;
- image build/update temporary space;
- backup duration, size, and restore duration;
- UI/API latency from shop-floor VLANs;
- behavior during NAS reboot, container restart, network interruption, and UPS
  shutdown.

Maintain disk alerts with enough headroom for the database, at least one upgrade
image set, temporary migration work, and the local backup staging window.

## 10. Migration from Synology to VPS

1. Provision the VPS using [`VPS.md`](./VPS.md) at the same application release
   and migration level.
2. Rehearse a dump/restore with staging data.
3. Schedule a production write freeze.
4. Create the final logical dump and checksum manifest.
5. Transfer it through an encrypted channel and verify the checksum.
6. Restore into the VPS PostgreSQL instance.
7. Run Alembic `current` and reconciliation checks before opening access.
8. Change internal DNS with a controlled TTL and test clients.
9. Keep the NAS instance stopped and recoverable until the rollback window
   closes; never allow both instances to accept writes.

