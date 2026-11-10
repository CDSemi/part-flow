# Deploying PartFlow on a Synology NAS

> **Language:** English is the source of truth. [Tiếng Việt](./SYNOLOGY_NAS.vi.md).

This guide installs PartFlow production on the company Synology NAS and then
shows how to update it, back it up and watch it. It is written for the person
who installs and runs PartFlow: someone with DiskStation Manager (DSM)
administrator access and basic Secure Shell (SSH) skills. The general rules for
every platform are in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) (§3.1 production
stack, §5 go-live requirements); the day-to-day procedures are in
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md). The production commands in
this guide have been run on Docker Desktop but not yet on a Synology NAS: when a
command fails on the NAS, fix it and record the fix here.

## 1. Start here

**Installing production:** check §2 and prepare the folders of §3, then follow
§8 from step 1. §8 sends you to §5.2 (reverse proxy and certificate) at the
right moment. After go-live, set up the backups of §6 and the monitoring of
§8 "Monitoring and alerts" when convenient; they are recommended and never
block use (owner decision 2026-11-09: production goes live directly, with no
pilot and no release gates, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5).

**Restricted staging (§4, §5.1, §7) is optional.** It runs the development
stack with synthetic or disposable data so a small internal group can try
PartFlow. It is never production, is never exposed to the internet, and must be
removed from the NAS before production is installed (§8 step 1). Production
always starts from a new, empty database; staging data is never carried over.

**Shell variable `PF`.** Every production command in this guide and in the
runbook uses this variable, set in the SSH session from the release checkout
(the `repo/` folder of §3):

```bash
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

`PF` is only a shorthand for the production Compose command. It is not the
Deployment Admin tool `pf` ([`SYNOLOGY_ADMIN.md`](./SYNOLOGY_ADMIN.md)), which is
not installed on this NAS.

## 2. Preflight checklist

Check and record these items before installing anything:

- the NAS model, processor architecture, installed memory, DSM version, storage
  pool and volume, file system, free space and redundant array of independent
  disks (RAID) health;
- **Container Manager** is offered for this NAS in Package Center and can create
  multi-container Projects from a Compose file;
- the processor architecture is supported by every image PartFlow uses (the
  PostgreSQL, Python, Node.js and nginx base images);
- enough memory and processor time remain after the NAS's existing workloads
  (§9 explains how to measure);
- the NAS has a reserved local area network (LAN) address and correct domain
  name system (DNS) and network time protocol (NTP) settings;
- the PartFlow folders (§3) live on a protected volume;
- the DSM firewall and the company network can restrict PartFlow to the intended
  subnets or virtual LANs (VLANs);
- an uninterruptible power supply (UPS) is connected, and the automatic safe
  shutdown is configured and tested;
- at least one encrypted backup destination exists outside this NAS;
- **Git:** the production scripts read the checked-out git tag, so production
  needs a git checkout, not an archive; on DSM the `git` command comes from the
  **Git Server** package in Package Center;
- **one operating account:** choose one account that can run `docker ps` over
  SSH without a permission error, and run every PartFlow command, `release.sh`
  and every scheduled task (§6, §8) as that account. On DSM the Docker socket
  usually belongs to `root`, so this is often `root` (`sudo -i` after signing in
  with a named administrator). The account needs a home folder: the scripts keep
  release records in `~/partflow-deployments` and monitoring state in
  `~/partflow-monitoring`.

If Container Manager is not available for this NAS, do not install unsupported
Docker packages and do not weaken DSM security. Use a Linux virtual machine or
a virtual private server (VPS) instead ([`VPS.md`](./VPS.md)).

## 3. Directory plan

Create one dedicated shared folder for PartFlow on the volume the administrator
chose. Do not assume the volume is `volume1`. Example:

```text
<NAS_VOLUME>/docker/partflow/
  repo/          production release checkout; .env.production lives here (mode 600)
  secrets/       production secret files only (PARTFLOW_SECRETS_DIR, mode 0700)
  backups/       production backups only (PARTFLOW_BACKUP_DIR, mode 0700)
  staging/       optional restricted staging (§4): its own checkout and dumps
```

Rules:

- `secrets/` and `backups/` are outside the checkout and are used by production
  only; staging never reads them and never writes to them.
- Only the operating account (§2) and the account Container Manager uses need
  write access. Ordinary DSM users need no access at all.
- Never place `.env`, `.env.production`, secret files, database dumps or backups
  in a web-served shared folder.
- Never grant the `Everyone` group read or write permission.

## 4. Restricted staging deployment (optional)

Skip this section when you install production directly. Staging uses the
development Compose file `compose.yaml`, which publishes its development servers
and is not hardened. Run every staging command from `staging/repo/`. Always give
the project name `partflow-staging` (`-p partflow-staging`): `compose.yaml`
names its project `partflow` by default, and §8 step 1 and `release.sh` look
for `partflow-staging`.

### 4.1 Obtain a pinned revision

Enable SSH for the installation if company policy permits, sign in with a named
administrator account and clone the repository. Check out an explicit commit or
tag, never a moving branch, and record the resolved commit:

```bash
cd <NAS_VOLUME>/docker/partflow/staging
git clone https://github.com/CDSemi/part-flow.git repo
cd repo
git fetch --tags --prune
git checkout <approved-commit-or-tag>
git rev-parse HEAD
```

### 4.2 Configure staging secrets

Copy the example environment file and protect it. The real `.env` is never
committed.

```bash
cp .env.example .env
chmod 600 .env
```

Then edit `.env`:

- set `POSTGRES_PASSWORD` to a unique, long password used only for staging;
  never reuse a DSM, GitHub, production or personal password;
- keep a protected copy of it in the company password vault;
- set `SITE_TIMEZONE` to the factory's time zone name (for example
  `America/Los_Angeles`) before the first start. The backend refuses an unknown
  zone name, and the done dates and on-time or late outcomes of completed Work
  Orders are judged on this calendar. Production must later use the same value.

`compose.yaml` builds the backend's database connection from `POSTGRES_USER`,
`POSTGRES_PASSWORD` and `POSTGRES_DB`, so no other database setting is needed.

### 4.3 Limit published ports

`compose.yaml` publishes PostgreSQL on port `5432`, the backend on `8000` and
the frontend on `5173` on every NAS address. Staging users need only the
frontend, because the frontend's development server forwards `/api` requests to
the backend. Before the first start, edit `compose.yaml` in this staging
checkout:

1. delete the `ports` entries of the `db` and `backend` services;
2. change the `frontend` entry to the NAS LAN address only:

   ```yaml
   ports:
       - "<NAS LAN address>:5173:5173"
   ```

3. leave the Compose service network private (no other change);
4. in DSM, Control Panel → Security → Firewall, allow port `5173` only from the
   intended company subnets.

Staging is reached at `http://<NAS LAN address>:5173`, not through a host name;
§5.1 explains why. Never commit these NAS-specific changes or secrets back to
the repository.

### 4.4 Create the Container Manager Project

In DSM:

1. Install and open **Container Manager**.
2. Open **Project** → **Create**.
3. Name the project `partflow-staging`.
4. Select `staging/repo/` as the path and the edited `compose.yaml`.
5. Review the services, mounts, networks, ports and environment values.
6. Build and start the Project.

The equivalent SSH commands are:

```bash
docker compose -p partflow-staging up -d --build
docker compose -p partflow-staging ps
docker compose ls
```

`docker compose ls` must list the project as `partflow-staging`. If it shows
`partflow`, stop it with `docker compose -p partflow down` (without `-v`) and
start it again with `-p partflow-staging`.

### 4.5 Apply migrations and first-run setup

Wait until `db` and `backend` are healthy, then upgrade the database schema
once and capture the output:

```bash
docker compose -p partflow-staging exec backend uv run alembic current
docker compose -p partflow-staging exec backend uv run alembic upgrade head
docker compose -p partflow-staging exec backend uv run alembic current
```

Never run `alembic downgrade` on staging data you want to keep without first
reviewing the migrations involved and taking a verified dump (§6).

A new database has no Administrator. Read the setup token and complete first-run
setup before anyone else gets the address:

```bash
docker compose -p partflow-staging logs backend | grep "Setup token"
```

Open `http://<NAS LAN address>:5173`, use **Set up PartFlow** with the token,
then enroll each Scan Station device in Administration → Scan Stations →
`Devices…`.

### 4.6 Smoke test

On the NAS, use the LAN address: with the binding of §4.3, nothing listens on
`127.0.0.1:5173`.

```bash
curl --fail --silent --show-error http://<NAS LAN address>:5173/api/health
docker compose -p partflow-staging ps
docker compose -p partflow-staging logs --tail=200 backend frontend db
```

From an allowed workstation:

1. open `http://<NAS LAN address>:5173` and check the connected indicator;
2. check that the views you intend to test load, and note any that are still
   unavailable;
3. with designated test records, exercise create, release, transfer, Machine,
   correction, stocking and allocation;
4. check that each success is confirmed by the server and the view refreshes;
5. stop the backend briefly (`docker compose -p partflow-staging stop backend`),
   check that writes are blocked, start it again (`… start backend`) and check
   that the screens recover;
6. check that a computer outside the allowed subnets cannot connect.

### 4.7 Staging restrictions

- no real production quantities and no personal employee data;
- no internet or network address translation (NAT) port forwarding, no
  QuickConnect publication and no public tunnel;
- named testers only, with someone watching during test sessions;
- regular disposable dumps (§6) so migration and update tests can be repeated;
- tell testers clearly that the address is staging.

## 5. DSM Reverse Proxy for internal HTTPS

DSM's reverse proxy receives HTTPS (Transport Layer Security, TLS) connections
for a host name and forwards them to a local service. §5.1 covers the staging
case and §5.2 the production `web` service.

### 5.1 Staging (development stack)

**The DSM reverse proxy cannot be used for staging with the current development
configuration.** The staging frontend is the Vite development server (Vite 6,
`frontend/package.json`). Vite answers only requests whose `Host` header is
`localhost`, a name under `.localhost` or an IP address, and refuses every
other host name with HTTP 403 and a "Blocked request" message, unless the
name is listed in `server.allowedHosts`. `frontend/vite.config.ts` lists none, and the DSM reverse proxy forwards the host name the browser used,
so a request through `https://<staging name>` is refused.

Reach staging at `http://<NAS LAN address>:5173` (§4.3) instead. This works
because the `Host` header is then an IP address, and the development backend
does not require HTTPS for the sign-in cookie. The traffic is unencrypted, which
is one more reason staging holds only synthetic or disposable data. Do not work
around the check by editing `vite.config.ts` on the NAS.

### 5.2 Production `web`

`compose.production.yaml` publishes the production `web` service on
`127.0.0.1:${PARTFLOW_HTTP_PORT}` only (default `18080`), so the DSM reverse
proxy is the only way in. HTTPS is required: production marks the sign-in
cookie secure, so browsers keep it only over HTTPS. The required values come
from [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1.

**Reverse proxy rule.** In Control Panel → Login Portal → Advanced → Reverse
Proxy → **Create**:

| Setting | Value |
| --- | --- |
| Source protocol, host name, port | HTTPS, the PartFlow host name, `443` |
| Destination protocol, host name, port | HTTP, `127.0.0.1`, the value of `PARTFLOW_HTTP_PORT` |
| Custom Header | `X-Forwarded-For` = `$proxy_add_x_forwarded_for` and `X-Forwarded-Proto` = `$scheme` (confirm or add) |
| Advanced Settings: proxy send and read timeouts | `300` seconds (above `web`'s 180-second import timeout) |
| Advanced Settings: request body size | at least 5 MiB (above `web`'s 4 MiB limit) |

Why these values:

- use `127.0.0.1`, never `localhost`, because `localhost` may resolve to the
  IPv6 address first, where `web` does not listen;
- `X-Forwarded-For` puts the client address last so `web` can rate-limit per
  client; if DSM cannot send it, every client shares one rate-limit bucket, so
  record that limitation;
- shorter timeouts or a smaller body limit make image uploads and Work Order
  imports fail.

Also:

- redirect HTTP to HTTPS;
- never log cookies or the `X-PartFlow-Station-Device` and `X-PartFlow-CSRF`
  headers;
- in the DSM firewall and the upstream network, admit only the approved LAN or
  virtual private network (VPN) sources;
- do not expose DSM administration through the PartFlow host name.

**Certificate.** The certificate must name the PartFlow host name and be issued
by a public certificate authority (CA) that uses the Automatic Certificate
Management Environment (ACME) protocol, by the company's internal CA, or by a
private CA. Users must never click past a browser warning instead
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1). In Control Panel → Security →
Certificate → **Add**, choose one option:

1. **Import certificate** from the company CA: import the certificate, its
   private key and the intermediate chain.
2. **Get a certificate from Let's Encrypt**, when the host name is a public DNS
   name. DSM renews it automatically.
3. **Create self-signed certificate**, when neither is available. DSM first
   creates a root certificate (your private CA), then the PartFlow certificate
   signed by it; give the PartFlow host name as the common name and as the
   subject alternative name (SAN). Export the root certificate.

Then open **Settings** in the same page and assign the certificate to the
PartFlow reverse-proxy host name. For options 1 and 3, install the root
certificate of the company CA or the private CA as trusted on every workstation
and every Scan Station device, through the company's device management or by
hand. DSM renews only Let's Encrypt certificates: replace any other certificate
before it expires and assign it again. Check the expiry date with the
`openssl s_client` command in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1; the
`certificate` check of `check.sh` warns before expiry (§8 Monitoring and
alerts).

## 6. Backups

### Production backups

Production uses the backup format of `deploy/production/backup.sh`: one folder
per backup with a manifest and a `SHA256SUMS` checksum file
([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3). Backups are
recommended after go-live and never block use; until the daily task runs, the
only backups are the ones `release.sh` takes before each release.

1. **Backup directory.** Use the `backups/` folder of §3 (for example
   `<NAS_VOLUME>/docker/partflow/backups`). It must be outside the release
   checkout, the secrets folder and any archive folder, owned by the operating
   account (§2), with mode 0700. Set it, unquoted, as `PARTFLOW_BACKUP_DIR` in
   `.env.production` before the first `$PF` command (§8 step 2). It holds
   production backups only, never staging's or a restore drill's.
2. **Daily backup task.** Control Panel → Task Scheduler → Create → Scheduled
   Task → User-defined script. User: the operating account. Schedule: daily at
   02:00. Task Settings → Run command:

   ```bash
   cd <running release checkout> && deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
   ```

   `--keep-daily` and `--keep-weekly` are required; 14 and 8 are examples, and
   the owner chooses the real retention. Enable "Send run details by email"
   **only when the script terminates abnormally**, so every non-zero exit (a
   refused, failed or unverifiable backup, or `backup_lock_stale`) reaches an
   administrator. Keep this time outside release windows
   ([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5): a running backup
   stops a release, and a daily backup that starts during a release is refused.
   With a daily schedule the recovery point objective (RPO), the most data that
   can be lost, is 24 hours.
3. **Encrypted copy off the NAS (Hyper Backup).** Create a Hyper Backup task
   whose source is the backup directory and whose destination is outside this
   NAS (a remote NAS, Synology C2 or an S3-compatible service). Turn
   **client-side encryption on**; the owner keeps the password and the key file
   off the NAS, because losing them loses the off-NAS copies. Schedule it at
   03:00, after the backup. Set Hyper Backup's own version rotation as the owner
   decides, run its integrity check weekly and enable email notification on
   failure. Reviewing these notifications is part of the routine of
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §9. Local snapshots are
   only a second layer.
4. **Archive directory (planned).** PartFlow does not create archives yet. When
   it does, the archive folder gets its own Hyper Backup task with **no version
   rotation**: no tool ever rotates or prunes an archive.
5. **Restore drill.** Once after go-live and then every quarter, run from the
   release checkout:

   ```bash
   deploy/production/restore-test.sh --backup <latest daily backup> --operator "<name>"
   ```

   It restores into an isolated project and never touches the live database
   ([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §4). Keep its evidence;
   its timings tell you the recovery time to expect.

### Staging dumps

Staging (§4) uses a plain PostgreSQL dump instead. Run from `staging/repo/`; the
variables in single quotes expand inside the database container, not in the
DSM shell:

```bash
mkdir -p ../backups
docker compose -p partflow-staging exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > ../backups/partflow-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Record a manifest beside each dump:

```bash
git rev-parse HEAD
docker compose -p partflow-staging exec -T backend uv run alembic current
sha256sum ../backups/partflow-*.dump
```

A snapshot of a running PostgreSQL volume is not a database backup unless a
documented and tested database-consistent snapshot procedure is used.

## 7. Staging update procedure

To move staging (§4) to a newer revision:

1. Tell the testers when staging will be unavailable.
2. Record the current commit (`git rev-parse HEAD`) and Alembic revision.
3. Take and check a fresh dump (§6, Staging dumps).
4. Fetch and check out the approved target commit (`git fetch --tags --prune`,
   `git checkout <target>`), then reapply the port changes of §4.3 if the
   checkout replaced them.
5. Review the changes to `.env.example`, `compose.yaml`, the Dockerfiles, the
   dependency lock files and the migrations.
6. Build and start: `docker compose -p partflow-staging up -d --build`.
7. Run `alembic upgrade head` once (§4.5).
8. Run the smoke test of §4.6.
9. Keep the previous commit and the dump until the testers confirm the update.

Never use an unattended "latest" or `main` auto-updater for a system with a
database. Production updates follow §8, Updating production.

## 8. Production (go-live)

Production is the production stack: `compose.production.yaml`,
`.env.production.example`, the `production` stages of `backend/Dockerfile` and
`frontend/Dockerfile`, and `frontend/nginx/`. It is never staging with a new
address. [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5 lists what the software needs
in order to work; everything else is recommended after go-live. Run every step
over SSH as the operating account (§2), from `repo/` once step 2.1 has created it.

1. **Remove staging from this NAS.** `release.sh` refuses to run while a
   `partflow-staging` project has containers on the Docker daemon (environment
   separation, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §6). Stop and remove the
   staging containers **without** `-v`:

   ```bash
   docker compose -p partflow-staging down
   docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u
   ```

   The list must contain neither `partflow-staging` nor `partflow` (a
   development project started without `-p`; remove it the same way with
   `-p partflow`). The staging volume holds only staging data and is never
   attached to production; delete it when it is no longer needed:
   `docker volume rm partflow-staging_postgres_data`. Then delete the Container
   Manager Project `partflow-staging` and close firewall port `5173`.
2. **Get the release and create the configuration.**
   1. Clone the repository into `repo/` and check out the release tag
      (form `vMAJOR.MINOR.PATCH`, for example `v1.0.0`;
      [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §10):

      ```bash
      cd <NAS_VOLUME>/docker/partflow
      git clone https://github.com/CDSemi/part-flow.git repo
      cd repo
      git checkout <release tag>
      cp .env.production.example .env.production
      chmod 600 .env.production
      PF="docker compose -f compose.production.yaml --env-file .env.production"
      ```

   2. Create the secrets and backup folders, each owned by the operating account:

      ```bash
      install -d -m 0700 <NAS_VOLUME>/docker/partflow/secrets
      install -d -m 0700 <NAS_VOLUME>/docker/partflow/backups
      ```

   3. Fill every empty value in `.env.production`: `PARTFLOW_RELEASE` (the
      release tag), `PARTFLOW_SECRETS_DIR` and `PARTFLOW_BACKUP_DIR` (the two
      absolute paths above, unquoted) and `PARTFLOW_SITE_TIMEZONE` (step 3).
      Leave `PARTFLOW_COMMIT`, `PARTFLOW_ACCEPT_SCHEMA_REVISION` and
      `PARTFLOW_TRUSTED_PROXY` empty. Check that `PARTFLOW_EDGE_SUBNET`
      (default `172.30.250.0/24`) is not used on the NAS or the LAN. Every
      `$PF` command needs `PARTFLOW_BACKUP_DIR`, so set it first.
   4. Create the three secret files in the secrets folder **before any `$PF`
      command that starts `backend` or an ops service**, because Compose
      replaces a missing secret file with an empty directory:
      `postgres_password`, `partflow_app_password` and
      `partflow_maintenance_password`. Each holds exactly one line and has mode
      0444. The two role passwords are 16 to 128 printable ASCII characters
      without spaces and differ from each other. Keep a copy of each in the
      company password vault.

      ```bash
      cd <NAS_VOLUME>/docker/partflow/secrets
      openssl rand -base64 32 > postgres_password
      openssl rand -base64 32 > partflow_app_password
      openssl rand -base64 32 > partflow_maintenance_password
      chmod 0444 postgres_password partflow_app_password partflow_maintenance_password
      cd <NAS_VOLUME>/docker/partflow/repo
      ```

   5. Validate the configuration: `$PF config --quiet` prints nothing when it
      is valid.
3. **Check the time zone.** `PARTFLOW_SITE_TIMEZONE` is the factory's time zone
   name (for example `America/Los_Angeles`) and must equal the `SITE_TIMEZONE`
   staging used, if staging existed.
4. **Build, then create the database.** Run in this order:

   ```bash
   PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
   $PF up -d db
   $PF --profile ops run --rm -T db-roles
   $PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"
   ```

   The build makes both images (`backend` and `web`); never build only one.
   `db-roles` creates the database roles `partflow_app` and
   `partflow_maintenance`; the backend connects as the least-privilege role
   `partflow_app`, never as the database owner. `migrate` creates the schema and
   applies the grants. The volume `partflow-production_postgres_data` is new and
   empty: staging data is never promoted. Restoring an existing backup into
   production instead follows [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md)
   §6 path 3 or §8 Host failure, with the owner's approval recorded before
   `backend` starts.
5. **Start the application with one backend worker, and open HTTPS.**

   ```bash
   PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web
   curl --fail --silent --show-error http://127.0.0.1:18080/api/health
   ```

   One worker ensures there is a single setup token. Use the port you set in
   `PARTFLOW_HTTP_PORT` in the `curl` command; `127.0.0.1` is correct here
   because `web` listens only on the loopback address. Then create the reverse
   proxy rule and the certificate of §5.2, and install the root certificate on
   the workstations and Scan Station devices if your certificate needs it.
6. **First-run setup.** Read the token, open `https://<PartFlow host name>`,
   use **Set up PartFlow**, then restart the backend with the configured worker
   count:

   ```bash
   $PF logs backend | grep "Setup token"
   $PF up -d backend
   $PF run --rm --no-deps -T backend python -m app.cli reconcile
   ```

   The reconciliation must report check (h) as `pass`. Then enroll each Scan
   Station device in Administration → Scan Stations → `Devices…`.
7. **Never run `down -v`**, and never remove a volume, on the
   `partflow-production` project: it deletes the production database. `$PF down`
   without `-v` is the only safe way to stop everything.

After go-live, when convenient, set up the recommended items of
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5: the daily backup and its encrypted
off-NAS copy (§6), one timed restore drill (§6), and the monitoring tasks below.

### Updating production

Every later release runs through `deploy/production/release.sh`. It takes a
verified backup, builds the new images without touching the running ones,
migrates the database, switches the application and runs the smoke checks. The
details, options and outcomes are in
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5 and
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §7. In `repo/`:

```bash
git fetch --tags --prune
git checkout <new release tag>
deploy/production/release.sh --release <new release tag> --operator "<name>" --approver "<name>"
```

One person may give the same name as operator and approver. `.env.production`
is not tracked by git, so it stays in place; `release.sh` writes the new tag
into it at the switch. Keep the previous release's images until the
observation window ends (no `docker image prune -a`), because rolling back
needs them. If you use a new checkout folder for each release instead of
`repo/`, copy `.env.production` into it and update the checkout path in every
scheduled task after the release.

### Monitoring and alerts

Configure Control Panel → Notification → Email first, so the Task Scheduler's
failure mail can leave the NAS. Then create two tasks in Control Panel → Task
Scheduler → Create → Scheduled Task → User-defined script, as the operating
account that owns the backup directory:

| Task name | Schedule | Run command |
| --- | --- | --- |
| `PartFlow check` | daily from 00:00, repeat every 15 minutes until 23:45 | `cd <running release checkout> && deploy/production/check.sh --url https://<partflow host> --quiet` |
| `PartFlow reconcile` | daily at 04:00 (after the 02:00 backup) | `cd <running release checkout> && deploy/production/scheduled-reconcile.sh` |

In both tasks enable "Send run details by email" **only when the script
terminates abnormally**. `check.sh --quiet` prints nothing and exits 0 unless a
failure must be reported, and it repeats an unchanged failure only every 6
hours. Add `--resolve-to 127.0.0.1` to the check task when the NAS cannot
resolve its own PartFlow host name.

Test the email path once: temporarily add
`--only disk_backup --min-free-percent 100 --renotify-hours 0` to the check
task's command, run the task once from Task Scheduler (**Run**), confirm that
the failure email arrives, then remove the added options. A minimum of 100 %
free space fails every measured disk on purpose. The thresholds, exit codes
and the meaning of each line are in [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §2 and §9.

## 9. Capacity and reliability

A generic memory or processor number does not prove that the NAS is ready.
Measure on this NAS, during staging tests or the first weeks of production:

- idle and peak processor and memory use of every PartFlow service and of the
  NAS's existing workloads;
- PostgreSQL data and index growth;
- temporary space for image builds and updates;
- backup duration and size, and restore duration (the progress lines of
  `backup.sh` and the `evidence.json` file of `restore-test.sh`);
- screen and API response time from the shop-floor networks;
- behaviour during a NAS reboot, a container restart, a network interruption and
  a UPS shutdown.

Use these measurements to set the resource limits in `.env.production`:

| Setting | Default |
| --- | --- |
| `PARTFLOW_DB_MEMORY`, `PARTFLOW_DB_CPUS` | `1g`, `1.0` |
| `PARTFLOW_BACKEND_MEMORY`, `PARTFLOW_BACKEND_CPUS` | `1g`, `2.0` |
| `PARTFLOW_WEB_MEMORY`, `PARTFLOW_WEB_CPUS` | `128m`, `0.5` |
| `PARTFLOW_OPS_MEMORY`, `PARTFLOW_OPS_CPUS` | `512m`, `1.0` |

Keep enough free disk space for the database, at least one extra set of
release images, temporary migration work and the local backups. The disk
checks of `check.sh` alert below 15 % free space:

| Check | Measures |
| --- | --- |
| `disk_data` | the database volume |
| `disk_backup` | the backup directory |
| `disk_docker` | the Docker root (images and container logs) |

Database growth is recorded in `growth.tsv` in the check state folder
(`~/partflow-monitoring`). A backup needs free space of twice the newest dump
plus 1 GiB in the backup directory, and a restore drill needs five times the
dump plus 1 GiB on the Docker root (the drill runs one backend worker).

## 10. Migration from Synology to VPS

1. Prepare the VPS with [`VPS.md`](./VPS.md) at the same application release
   and migration level as the NAS.
2. Rehearse on the VPS with a recent production backup:
   `deploy/production/restore-test.sh --backup <name> --operator "<name>"`.
3. Announce and start a production write freeze on the NAS: `$PF stop backend`.
4. Take the final backup inside the freeze:
   `deploy/production/backup.sh --kind manual --operator "<name>" --reason "move to VPS"`.
   Its `SHA256SUMS` file is the checksum manifest.
5. Copy the backup folder to the VPS over an encrypted channel and verify it
   there with `sha256sum -c SHA256SUMS` and `backup-verify`.
6. Restore into the VPS PostgreSQL with the new-instance restore of
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 (a new, empty
   `postgres_data` volume; the owner's approval recorded before `backend`
   starts).
7. Run `revision` and the reconciliation checks before opening access.
8. Change the internal DNS record with a short time to live (TTL) and test the
   clients.
9. Keep the NAS instance stopped and recoverable until the rollback window
   closes. Never let both instances accept writes.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | not yet committed | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-09 | 03bd90c | Added the monitoring tasks and the disk thresholds of the host checks. |
| 2026-10-08 | a330383 | Added the production backup tasks, the off-NAS encrypted copy and the restore drill. |
| 2026-10-08 | 1116b07 | Added the production database roles to the installation steps. |
| 2026-10-08 | b820e7a | Added the release flow with `release.sh` for later releases. |
| 2026-10-08 | d7e86a9 | Added the production stack, the DSM reverse proxy settings and the certificate options. |
| 2026-10-06 | 702ab1c | Added first-run Administrator setup and Scan Station device enrollment. |
| 2026-09-02 | d2f68de | First version of the Synology NAS guide with its Vietnamese mirror. |
