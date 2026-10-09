# Deploying PartFlow on a Synology NAS

> **Status:** The current repository supports a restricted internal staging
> deployment only. The production path in this document becomes actionable when
> the Phase 14 and Phase 16 gates in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) are
> complete.
>
> **Language:** English is the source of truth. [Tiếng Việt](./SYNOLOGY_NAS.vi.md).

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
  repo/                 checked-out release (.env.production lives here, mode 600)
  secrets/              production only, mode 0700 (PARTFLOW_SECRETS_DIR)
  backups/
    database/
    manifests/
  restore-tests/
```

`secrets/` is outside the checkout, holds only the production secret files
(`postgres_password` in P16-S2) and is never shared with staging.

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

On a new database with no Administrator, complete first-run setup before opening
access: read the setup token with `docker compose logs backend | grep "Setup token"`
and use **Set up PartFlow** in the application. Then enroll each Scan Station
device in Administration → Scan Stations → `Devices…`.

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

This section has two cases: restricted staging on the development stack (§5.1)
and the production `web` tier (§5.2).

### 5.1 Staging (development stack)

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

### 5.2 Production `web` (Phase 16)

The production `web` tier is published on the NAS loopback address only, so the
DSM reverse proxy is the only HTTPS entry point. The settings below are the
required values from [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1; they are
**documented in P16-S2 and executed and verified on the NAS in P16-S7**.
`compose.production.yaml` publishes `web` on `127.0.0.1:${PARTFLOW_HTTP_PORT}`
only; nothing in this section has been run on a Synology NAS yet.

- Control Panel → Login Portal → Advanced → Reverse Proxy: source HTTPS, the
  PartFlow hostname, port 443; destination HTTP, host `127.0.0.1` (never
  `localhost`, which may resolve to IPv6 first), port = the loopback port the
  production Compose project publishes;
- Custom Header: confirm or add `X-Forwarded-For` = `$proxy_add_x_forwarded_for`
  and `X-Forwarded-Proto` = `$scheme`, so the client address arrives last and
  `web` can rate-limit per client; if DSM cannot supply it, every client shares
  one rate-limit bucket (record it for the owner);
- Advanced Settings: proxy send and read timeouts of 300 s (above `web`'s
  180 s import timeout); request body of at least 5 MiB (above `web`'s 4 MiB);
- redirect HTTP to HTTPS; never log cookies, `X-PartFlow-Station-Device` or
  `X-PartFlow-CSRF`; DSM firewall and upstream rules admit only the approved
  LAN or VPN sources; do not expose DSM administration through the PartFlow
  hostname.

**Certificate (DSM).** The certificate follows the common rules of
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 (names the hostname, issued by a
public ACME CA or the company's internal CA, never accepted per workstation
past a warning). Control Panel → Security → Certificate → **Add** → import the
certificate, its private key and the intermediate chain (internal CA), or **Get
a certificate from Let's Encrypt** (public name; DSM renews it automatically).
Then **Settings** → assign the certificate to the PartFlow reverse-proxy
entry's hostname. DSM does not renew an imported certificate: import the new one
before expiry and re-assign it. Distribute the internal CA to workstations and
barcode terminals through the company's device management, and check expiry
with the `openssl s_client` command in DEPLOYMENT §3.1 (expiry alert:
`check.sh` `certificate`, §8 Monitoring and alerts).
Executed and verified in P16-S7.

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

### Production backups (Phase 16)

The production stack uses the P16-S5 backup artifact, not the staging dump
above ([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3; one directory per
backup with a manifest and `SHA256SUMS`). The scripts, the manifest and the
verification are implemented and exercised on Docker Desktop; the task
definitions below are **configured and executed on the NAS in P16-S7**, and
nothing here has been run on a Synology NAS yet.

- **Backup directory.** A shared folder outside the release checkout, the
  secrets directory and any archive directory, for example
  `/volume1/partflow-backups/production`, mode 0700, owned by the account the
  scheduled task runs as (the account that runs `release.sh`). Set it as
  `PARTFLOW_BACKUP_DIR` in `.env.production` (unquoted) before the first `$PF`
  command. Production only: never staging's or a drill's.
- **Schedule (DSM Task Scheduler).** Create → Scheduled Task → User-defined
  script, user = that account, daily at 02:00, task settings → Run command:
  `cd <running release checkout> && deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8`
  (14 and 8 are placeholders; the owner sets the real retention, and the options
  are required). Enable "Send run details by email" **only when the script
  terminates abnormally**, so every non-zero exit (a refused, failed or
  unverifiable backup, `backup_lock_stale`) reaches an administrator. Keep the
  schedule outside maintenance windows (RUNBOOK §5). The recovery point
  objective is this schedule (24 hours) until the owner approves one.
- **Encrypted off-NAS copy (Hyper Backup).** A task whose source is the backup
  directory and whose destination is off-NAS (a remote NAS, C2 or an
  S3-compatible service), with **client-side encryption on**: the owner keeps the
  password and the key file off the NAS, and losing them loses the off-NAS
  copies. Schedule it after the backup (03:00), set the tool's own version
  rotation as the owner decides, run its integrity check weekly and enable
  email notification on failure. The review of these notifications is part of
  the daily routine (RUNBOOK §9). Local snapshots remain a second layer only.
- **Archive directory** (Phase 16 slice S8): it gets its own Hyper Backup task
  with **no version rotation**; archives are never rotated or pruned by any
  tool.
- **Restore drill.** Quarterly, `deploy/production/restore-test.sh --backup <latest daily> --operator "<name>"`
  (RUNBOOK §4); keep the evidence and compare its timings with the approved RTO.

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
the Phase 16 production artifacts and verify all production gates. State at
P16-S2 (implemented): `compose.production.yaml`, `.env.production.example`,
`backend/Dockerfile` and `frontend/Dockerfile` (`production` stages) and
`frontend/nginx/`; the commands below are the real ones
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1) but have **not been run on a
Synology NAS** (P16-S7). Items still pending are named with their slice below.
`PF` is `docker compose -f compose.production.yaml --env-file .env.production`,
run from the release checkout `repo/`.

1. **Remove staging from the pilot daemon.** pf-managed staging is not installed
   on this Docker daemon during the pilot (owner decision OD-16-01). Stop the
   manual `compose.yaml` staging project and remove its containers **without**
   `-v` (`docker compose -p partflow-staging down`); its volume is kept or
   deleted only by owner decision and is never attached to production. Prove it:
   `docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u`
   lists no `partflow-staging`.
2. **Create the configuration.** Copy `.env.production.example` to
   `.env.production` (mode 600) and fill every empty value. Create the secrets
   directory (`PARTFLOW_SECRETS_DIR`, mode 0700), the backup directory
   (`install -d -m 0700`, §6) with `PARTFLOW_BACKUP_DIR` set in
   `.env.production` **before any `$PF` command** (every Compose command of the
   stack needs it; a stack installed before P16-S5 is converted in the order of
   [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1), and the three one-line files
   `postgres_password`, `partflow_app_password` and
   `partflow_maintenance_password` (mode 0444; the two role files are 16 to 128
   printable ASCII characters without spaces and different from each other),
   before any `$PF` command that starts `backend` or an ops service. The backend
   connects as the least-privilege role `partflow_app` (P16-S4), never as the
   owner. Run `$PF config --quiet`.
3. **Check the time zone.** `PARTFLOW_SITE_TIMEZONE` must equal the staging
   `SITE_TIMEZONE`.
4. **Build, then start from a new empty volume.** `PARTFLOW_RELEASE` is the
   release tag (DEPLOYMENT §10). Build with
   `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build` (both images, never only `backend`), then `$PF up -d db`, then
   `$PF --profile ops run --rm -T db-roles` (creates `partflow_app` and
   `partflow_maintenance`), then
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`
   (which also applies the grants). A stack installed before P16-S4 is converted
   in the order of [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 (role secret
   files, `build backend web` with `PARTFLOW_COMMIT`, `db-roles`, `db-roles
   apply-grants`, then `release.sh`). The volume
   `partflow-production_postgres_data` is new and empty: staging data is never
   promoted, and a restore into production follows
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 path 3 or §8 host
   failure, with the owner's approval recorded before `backend` starts.
5. **First-run setup with one worker.**
   `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web`, read the token with
   `$PF logs backend | grep "Setup token"`, complete setup, then `$PF up -d backend`
   so the configured worker count applies. Then the DSM reverse proxy (§5.2) and
   enrollment of each Scan Station device. Later releases run through
   `deploy/production/release.sh` (DEPLOYMENT §3.1, Releases;
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5).
6. **Never run `down -v`** (or remove a volume) on the `partflow-production`
   project: it deletes the production database.

Pending: the execution of observability (implemented in P16-S6) with the NAS
host checks and the pilot gates (P16-S7); the release flow itself (`release.sh`,
`smoke.sh`), the backup and restore tooling (`backup.sh`, `restore-test.sh`,
P16-S5) and the monitoring scripts (`check.sh`, `scheduled-reconcile.sh`, P16-S6)
are implemented and not yet executed on the NAS.

- immutable production frontend and backend images — implemented (P16-S2:
  images tagged by `PARTFLOW_RELEASE`, never pulled); the release identity is
  inside the `web` image (P16-S3) and pending in the backend image (the open
  P16-S3 item);
- production Compose file with no source bind mounts, no reload/dev server, no
  published database port, and explicit restart/resource/logging policies —
  implemented (P16-S2; resource limits are starting values to measure in
  P16-S7);
- private backend/database networks and one reverse-proxy entry point
  (§5.2; host-verified in P16-S7);
- Phase 14 authentication/authorization;
- secret handling and separate least-privilege database roles — implemented
  (P16-S4: `partflow_app`, `partflow_maintenance`, `db-roles`, grants in every
  `migrate`; host evidence in P16-S7);
- scheduled logical backups, encrypted off-NAS replication, retention alerts,
  and successful restore drill — the scripts, the manifest, the verification, the
  retention and the drill are implemented (P16-S5); the schedule and the
  replication executed on the NAS and a drill timed there are pending (P16-S7),
  and the backup-age alert is implemented (P16-S6: `check.sh` `backup_age`) and
  installed on the NAS in P16-S7;
- monitoring for health, logs, disk, backup age, restart count, and database
  growth — implemented (P16-S6: `check.sh`, `status`), executed on the NAS in
  P16-S7;
- release, migration, rollback, reconciliation, and incident runbooks tested by
  the actual administrators;
- approved RPO/RTO and pilot scope.

### Monitoring and alerts (Phase 16)

The scripts are implemented (P16-S6) and exercised on Docker Desktop; the tasks
below are **configured and executed on the NAS in P16-S7**, and nothing here has
been run on a Synology NAS yet. Configure DSM Control Panel → Notification →
Email first, so the scheduler's failure mail can leave the NAS. Then, in Task
Scheduler → Create → Scheduled Task → User-defined script, as the account that
owns the backup directory:

1. `PartFlow check`: daily from 00:00, repeat every 15 minutes until 23:45; Run
   command: `cd <running release checkout> && deploy/production/check.sh --url https://<partflow host> --quiet`.
2. `PartFlow reconcile`: daily at 04:00 (after the 02:00 backup); Run command:
   `cd <running release checkout> && deploy/production/scheduled-reconcile.sh`.

Both tasks enable "Send run details by email" **only when the script terminates
abnormally**: `check.sh --quiet` prints nothing and exits 0 unless a `FAIL` is
due, and a repeated unchanged failure is reported again only every 6 hours. After
each release update the checkout path in both tasks. Add `--resolve-to 127.0.0.1`
to task 1 when the NAS cannot resolve its own PartFlow name. The notification
test is one run of task 1 with
`--only disk_backup --min-free-percent 100 --renotify-hours 0` (P16-S7 executes
it). The thresholds, the exit codes and what each line means are in
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §2 and §9.

## 9. Capacity and reliability

Do not treat a generic RAM/CPU number as proof of readiness. Measure during a
realistic staging test:

- idle and peak CPU/RAM for all services and existing NAS workloads;
- PostgreSQL data and index growth;
- image build/update temporary space;
- backup duration, size, and restore duration (the `backup.sh` progress lines
  and the `evidence.json` of `restore-test.sh`);
- UI/API latency from shop-floor VLANs;
- behavior during NAS reboot, container restart, network interruption, and UPS
  shutdown.

Maintain disk alerts with enough headroom for the database, at least one upgrade
image set, temporary migration work, and the local backup staging window (the
`check.sh` disk checks, P16-S6, alert below 15 % free: `disk_data` measures the
database volume, `disk_backup` the backup directory, `disk_docker` the Docker
root, and the database growth is the `growth.tsv` history in the check state
directory): a
backup needs `2 x` the newest dump plus 1 GiB free in the backup directory, and a
restore drill needs `5 x` the dump plus 1 GiB free on the Docker root (the drill
runs one backend worker).

## 10. Migration from Synology to VPS

1. Provision the VPS using [`VPS.md`](./VPS.md) at the same application release
   and migration level.
2. Rehearse a restore with a backup of staging data
   (`deploy/production/restore-test.sh`).
3. Schedule a production write freeze (`$PF stop backend`).
4. Create the final backup with
   `deploy/production/backup.sh --kind manual --operator "<name>" --reason "move to VPS"`
   inside the freeze (its `SHA256SUMS` is the checksum manifest).
5. Transfer the backup directory through an encrypted channel and verify it:
   `sha256sum -c SHA256SUMS` and `backup-verify`.
6. Restore into the VPS PostgreSQL instance with the new-instance restore of
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 (a new, empty
   `postgres_data` volume; the owner's approval recorded before `backend`
   starts).
7. Run Alembic `current` and reconciliation checks before opening access.
8. Change internal DNS with a controlled TTL and test clients.
9. Keep the NAS instance stopped and recoverable until the rollback window
   closes; never allow both instances to accept writes.

