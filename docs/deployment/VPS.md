# Deploying PartFlow on a Linux VPS

> **Language:** English is the source of truth. [Tiếng Việt](./VPS.vi.md).

This guide describes how to run PartFlow on a virtual private server (VPS) running Linux. A VPS is the alternative
production host: the host in use now is the company Synology NAS (see [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md)). Read
this guide when the NAS is no longer enough, or when the company needs an external host. Read
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) first: it owns the production stack, the configuration files and the go-live
requirements that this guide only applies to a VPS. Day-to-day operation (health checks, backups, restore, releases,
rollback) is in [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

Commands in this guide use the shell variable `PF`, which you define once in the release checkout:

```sh
PF="docker compose -f compose.production.yaml --env-file /srv/partflow/env/production.env"
```

`PF` is only a shorthand for the Docker Compose command of the production stack. It is not the Deployment Admin tool
`pf`. Run every `$PF` command from the directory of the release checkout. On a VPS the configuration file lives
outside the release directories, at `/srv/partflow/env/production.env` (§5), so every release, `$PF` command and
scheduled job reads the same file. The scripts and systemd units in this guide
have not yet been run on a VPS.

## 1. When to choose a VPS

Choose a VPS when PartFlow needs predictable Docker and Compose behavior, independent resource control, secure remote
access, provider snapshots in addition to application-level backups, or a clean upgrade path from the Synology NAS.
A VPS keeps the existing React, FastAPI and PostgreSQL architecture unchanged.

## 2. What the release provides

Never deploy production from `compose.yaml`: it is the development stack. The production release provides:

- production frontend and backend images (the `production` stages of the Dockerfiles; the web tier configuration is in
  `frontend/nginx/`);
- the production Compose configuration, `compose.production.yaml`
  ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
- an explicit migration job: the Compose service `migrate` (profile `ops`), which runs `python -m app.cli migrate` in
  one transaction;
- the configuration and secret inventory: `.env.production.example` and the secret files in
  `PARTFLOW_SECRETS_DIR` (DEPLOYMENT §3.1);
- the reverse proxy and certificate procedure (§4 below and DEPLOYMENT §3.1);
- release automation: `deploy/production/release.sh` and `smoke.sh`, the release identity (`revision`), and the
  readiness and liveness endpoints (see the runbook §5 and §6 for the release and rollback procedure, including
  rollback path 3);
- backup and restore automation: `deploy/production/backup.sh` and `restore-test.sh`
  (runbook §3 and §4);
- health, monitoring and reconciliation commands: `reconcile`, `check.sh`, `scheduled-reconcile.sh`, the `status`
  command and JSON logs that carry the HTTP request id (runbook §2 and §7).

## 3. Host baseline

The VPS needs:

- a supported 64-bit Linux distribution with current security updates;
- a dedicated non-root deployment account that signs in with SSH keys;
- Docker Engine and the Docker Compose plugin from a supported source;
- a host firewall that allows SSH from administrative sources and HTTP and HTTPS as approved, and denies every other
  inbound port;
- automatic security updates, or a documented patch window;
- correct time (Network Time Protocol, NTP) and timezone settings;
- separate persistent storage for PostgreSQL and for local backup staging;
- an encrypted provider or off-site backup destination;
- resource and disk monitoring: `check.sh` run by the systemd timer of §8, plus a manual review of CPU and memory
  with `docker stats`;
- no unrelated experimental workloads on the production host.

Never publish PostgreSQL to the internet. When the provider offers a private network, use it for remote backup or
database services.

## 4. DNS and TLS

Use a dedicated hostname such as `partflow.company.example`. Point the DNS record at the VPS only after the private
smoke test succeeds. The reverse proxy terminates Transport Layer Security (TLS) and forwards the whole origin to the
`web` tier of the Compose stack on the loopback address; `web` serves the built frontend and routes `/api` to the
backend ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 lists the proxy behavior the stack requires). After the proxy is
running, confirm that single-page application routes load directly and that `/api/health` answers through HTTPS.

If PartFlow is internal-only, restrict access with the firewall, a virtual private network (VPN) or private DNS.
Public reachability still requires full application authentication and authorization; a secret URL is not a control.

Example Caddy configuration on the host:

```caddyfile
partflow.company.example {
    request_body {
        max_size 5MiB
    }
    reverse_proxy 127.0.0.1:18080 {
        transport http {
            response_header_timeout 300s
        }
    }
}
```

Notes on the example:

- `18080` stands for `PARTFLOW_HTTP_PORT`, the loopback port that the production Compose project publishes.
- The destination is the literal `127.0.0.1`, never `localhost`.
- Caddy sets `X-Forwarded-For` to the client address and `X-Forwarded-Proto`, and redirects HTTP to HTTPS
  automatically.
- The body limit of 5 MiB and the timeout of 300 s sit above the 4 MiB and 180 s limits of `web`, so that `web` or the
  application answers with its own JSON error instead of the proxy cutting the request.
- If the loopback connection does not arrive from the gateway of the `edge` Compose network, set
  `PARTFLOW_TRUSTED_PROXY` to the address you observe (DEPLOYMENT §3.1). Check this on the VPS.

**Certificate (Caddy).** The common rules are in DEPLOYMENT §3.1.

- A public name gets an automatic certificate from a certificate authority (CA) through the ACME protocol, and renews
  it automatically, with no extra directive. Ports 80 and 443 must be reachable for the challenge.
- An internal-only name uses either `tls /etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (a certificate
  from the company CA; the files have mode 0600 and belong to the Caddy user; replace them and run `caddy reload`
  before they expire) or `tls internal` (Caddy's own CA, whose root certificate must then be distributed to every
  workstation like any internal CA).
- Check the expiry date with the `openssl s_client` command in DEPLOYMENT §3.1.

## 5. Filesystem layout

Example layout:

```text
/srv/partflow/
  releases/<immutable-release>/
  current -> releases/<immutable-release>/
  env/production.env
  data/
  backups/database/        = PARTFLOW_BACKUP_DIR (one directory per backup, mode 0700)
```

Each backup directory holds its own manifest; there is no separate `manifests/` directory. The deployment account owns
the release files. Secrets are readable only by the account or service that needs them. PostgreSQL data is never
inside a Git checkout.

## 6. Initial production deployment

1. Read the go-live requirements in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Provision and harden the host (§3).
3. Install the exact release files and images, and record their digest or commit.
4. Create the production secrets and configuration before any `$PF` command that starts `backend` or an ops service:
   - the three secret files `postgres_password`, `partflow_app_password` and `partflow_maintenance_password` in
     `PARTFLOW_SECRETS_DIR`, one line each ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
   - `/srv/partflow/env/production.env` (mode 600), copied from `.env.production.example` and filled in;
   - the backup directory, `install -d -m 0700 /srv/partflow/backups/database`, with `PARTFLOW_BACKUP_DIR` set to it
     (unquoted) in `/srv/partflow/env/production.env`: every `$PF` command needs that key.

   Then build both images:
   `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`.
5. Start PostgreSQL privately with `$PF up -d db`, then create the database roles with
   `$PF --profile ops run --rm -T db-roles`. The backend connects as `partflow_app`, never as the database owner.
6. Start from an empty database in a new volume. Production data never starts from staging or development data. A
   restore into production follows [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 (rollback path 3) or §8
   (host failure), with the owner's approval recorded before `backend` starts.
7. Run the migration once from the release backend image:
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`. It also applies the
   database grants.
8. Start the backend, `web` and the host reverse proxy (§4). On a database with no Administrator, start the backend
   with one worker, because first-run setup needs exactly one setup token:
   `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web`. Read the setup token from the backend log with
   `$PF logs backend | grep "Setup token"` and complete first-run setup before opening access. Then restart the
   backend with the configured worker count (`$PF up -d backend`) and enroll each Scan Station device
   (Administration → Scan Stations → `Devices…`).
9. Run the smoke and reconciliation checks of the runbook through HTTPS. The guard-integrity check, reconcile
   check (h), must report `pass`.
10. Recommended, and it never blocks use: enable the backup, check and reconcile timers and the alert unit of §8,
    then take a first backup immediately:
    `deploy/production/backup.sh --kind manual --operator "<name>" --reason "initial backup"`.
11. Recommended: perform and time an isolated restore of that backup
    ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5):
    `deploy/production/restore-test.sh --backup <that backup> --operator "<name>"`.
12. Open only the approved network sources and start using PartFlow.

## 7. Releases and rollback

Use immutable release directories and images. Build or pull the new release before stopping the old one. Back up
before a migration. Never deploy directly from a mutable `main` checkout.

Run `deploy/production/release.sh` from the new release directory with
`--env-file /srv/partflow/env/production.env`, so that it updates the one configuration file every scheduled job
reads. `release.sh` takes the verified pre-release backup inside the write freeze. Application rollback is allowed only when
the previous code is compatible with the migrated schema. If it is not, restore the pre-migration database and the
matching application release together (rollback path 3 of the runbook: a new database in the same PostgreSQL
instance, with the owner's approval recorded). An Alembic downgrade is not a generic rollback: PartFlow migrations can
protect immutable history by refusing destructive downgrades.

Follow [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 8. Backup strategy

Use PostgreSQL logical dumps as the portable baseline, and optionally add provider volume snapshots as a second
layer. Snapshots never replace a tested logical restore. The backup artifact, its verification, the retention rules
and the restore drill are described in [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3 and §4. A backup
strategy consists of:

- a scheduled custom-format `pg_dump` (`backup.sh`, one directory per backup);
- a checksum and a manifest that records the release commit and the Alembic revision;
- encryption in transit and at rest;
- an off-VPS copy with retention and a failure alert;
- a periodic restore into an isolated database;
- a recovery point objective (RPO) and a recovery time objective (RTO) measured from actual runs.

Backups and monitoring are recommended after go-live and never block use.

**Schedule (systemd).** Two units run the daily backup:

- `partflow-backup.service`: `Type=oneshot`, `User=<deploy account>`, `WorkingDirectory=/srv/partflow/current`,
  `OnFailure=` a unit that mails the administrators, and
  `ExecStart=/srv/partflow/current/deploy/production/backup.sh --kind daily --keep-daily 14 --keep-weekly 8 --operator scheduler --env-file /srv/partflow/env/production.env`.
  The values 14 and 8 are placeholders: the owner sets the real retention, and both options are required.
- `partflow-backup.timer`: `OnCalendar=*-*-* 02:00:00` and `Persistent=true`.

`ExecStart=` needs the absolute path, because `WorkingDirectory=` does not resolve a relative one and systemd refuses
the unit. Check both units with `systemd-analyze verify partflow-backup.service partflow-backup.timer`. A non-zero
exit, including a daily backup that fails verification, fails the unit. Keep the schedule outside maintenance windows,
because a release holds the backup lock.

**Monitoring (systemd).** Verify all five units with `systemd-analyze verify`, and use absolute `ExecStart=` paths as
above. `partflow-alert@.service` is the failure notifier of every unit:

```ini
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'journalctl -u %i -n 60 --no-pager | mail -s "PartFlow alert: %i failed on %H" <ops address>'
```

`%i` and `%H` are systemd specifiers (systemd never runs `$(...)`). The notifier needs a configured mail transfer
agent on the host, for example msmtp, and journal read access for the unit's user; both are host configuration.

The other two services use `Type=oneshot`, `User=<deploy account>`, `WorkingDirectory=/srv/partflow/current` and
`OnFailure=partflow-alert@%n.service`:

- `partflow-check.service` runs
  `ExecStart=/srv/partflow/current/deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env`;
  `partflow-check.timer` has `OnCalendar=*:0/15`.
- `partflow-reconcile.service` runs
  `ExecStart=/srv/partflow/current/deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env`;
  `partflow-reconcile.timer` has `OnCalendar=*-*-* 04:00:00` and `Persistent=true`.

The backup unit's `OnFailure=` is the same `partflow-alert@%n.service`. With cron instead of systemd, set `MAILTO=`
and use `--quiet` on both lines, because cron mails whenever a job prints anything:

```cron
*/15 * * * * cd /srv/partflow/current && deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env --quiet
0 4 * * * cd /srv/partflow/current && deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env --quiet
```

**Off-VPS copy (restic).** Set `RESTIC_REPOSITORY` to the object storage location and `RESTIC_PASSWORD_FILE` to a key
file of mode 0400. Keep the custody of that key off the VPS: losing it loses the copies. After the backup unit has
run:

1. `restic backup --tag database /srv/partflow/backups/database`
2. `restic forget --tag database --keep-daily 14 --keep-weekly 8 --prune` (the retention matches the `backup-rotate`
   retention)
3. `restic check`, weekly

A failure of any of these fails its unit (`OnFailure=`). When an archive directory is used, copy it to its own
repository or tag that `forget` never targets: archives are never rotated or pruned.

## 9. Moving from Synology

Use the backup and restore cutover in [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §10: it uses the same backup artifact
and the new-instance restore of the runbook §6. Keep the source and the target at the same release, enforce a write
freeze, verify the checksums and the reconciliation, and only then switch DNS. Never run two writable production
instances against divergent databases.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | `b405c6e` | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-09 | `03bd90c` | Added the monitoring units, the alert notifier and the cron alternative. |
| 2026-10-08 | `a330383` | Added production backup automation, the schedule units and the off-VPS restic copy. |
| 2026-10-08 | `1116b07` | Added the database role creation step to the initial deployment. |
| 2026-10-08 | `b820e7a` | Added the release flow and the rollback rule tied to the migrated schema. |
| 2026-10-08 | `d7e86a9` | Added the production Compose stack, the Caddy example and the certificate procedure. |
| 2026-10-06 | `702ab1c` | Added sign-in and first-run setup to the initial deployment. |
| 2026-09-02 | `d2f68de` | First version of the VPS guide with its Vietnamese mirror. |
