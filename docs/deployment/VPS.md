# Deploying PartFlow on a Linux VPS

> **Language:** English is the source of truth. [Tiếng Việt](./VPS.vi.md).

This guide installs PartFlow on a Linux virtual private server (VPS), the alternative production host. The host in
use now is the company Synology NAS ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md)); read this guide when the NAS is no longer
enough or the company needs an external host. Read [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §1 and §2 first. The
installation uses the same production stack and the same commands as the NAS guide; this guide gives them with the
VPS folders, adds Caddy for HTTPS and systemd for the schedules, and refers to the NAS guide only for identical
explanations. Day-to-day operation is in [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md). The scripts and systemd
units in this guide have not yet been run on a VPS: correct the guide when a step behaves differently.

## 1. When to choose a VPS

Choose a VPS when PartFlow needs predictable Docker behaviour, its own processor and memory, secure remote access,
provider snapshots in addition to the PartFlow backups, or a host outside the company. The application stays the
same: the same images, Compose file, scripts and backups.

## 2. Host requirements

- A supported 64-bit Linux distribution with current security updates, and automatic security updates or a fixed
  patch window.
- Docker Engine and the Docker Compose plugin from a supported source, plus `git`, `curl`, `openssl` and Python 3.8
  or newer (`python3`).
- A dedicated deployment account that signs in with SSH keys and may run Docker. It is the operating account
  ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §2.1): it runs every command, owns the backup folder and runs the schedules.
- A host firewall that admits SSH from the administrators' addresses and HTTP and HTTPS as decided, and refuses every
  other port. Never publish PostgreSQL; use the provider's private network for any remote service.
- Correct time (network time protocol, NTP) and enough disk for the database, the images and the local backups.
- An encrypted backup destination outside the VPS (§8), and no unrelated workloads on the production host.

## 3. Folder layout and the `PF` shorthand

```text
/srv/partflow/
  releases/<tag>/        one release checkout per release
  current                link to the running release's checkout; the schedules run from it
  env/production.env     the configuration, outside every checkout (mode 600)
  secrets/               the three password files (PARTFLOW_SECRETS_DIR, mode 0700)
  backups/database/      one sub-folder per backup (PARTFLOW_BACKUP_DIR, mode 0700)
```

The deployment account owns everything under `/srv/partflow`. Because the configuration file lives outside the
checkouts, every release, every `$PF` command and every scheduled job reads the same file. Every script therefore
needs `--env-file /srv/partflow/env/production.env`, and `PF` is:

```sh
PF="docker compose -f compose.production.yaml --env-file /srv/partflow/env/production.env"
```

## 4. Installing PartFlow

Run these steps as the deployment account. `<TAG>` is the release tag, `<TIMEZONE>` the factory's time zone (for
example `America/Los_Angeles`). Each step matches the NAS guide section named in brackets, which explains what you
should see.

1. Create the folders:

   ```sh
   sudo mkdir -p /srv/partflow/releases /srv/partflow/env /srv/partflow/secrets /srv/partflow/backups/database
   sudo chown -R "$(id -un):$(id -gn)" /srv/partflow
   chmod 700 /srv/partflow/secrets /srv/partflow/backups/database
   ```

2. Download the release and mark it as the running one ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §5 step 1):

   ```sh
   cd /srv/partflow/releases
   git clone https://github.com/CDSemi/part-flow.git <TAG>
   cd <TAG>
   git checkout <TAG>
   ln -sfn /srv/partflow/releases/<TAG> /srv/partflow/current
   ```

3. Create the configuration file (NAS guide §5 steps 2 and 3). The last command checks that the subnet of the
   internal `edge` network is unused: it must print nothing; otherwise choose another subnet for
   `PARTFLOW_EDGE_SUBNET` as the NAS guide §5 step 3 shows.

   ```sh
   cp .env.production.example /srv/partflow/env/production.env
   chmod 600 /srv/partflow/env/production.env
   E=/srv/partflow/env/production.env
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<TAG>|' "$E"
   sed -i 's|^PARTFLOW_SECRETS_DIR=.*|PARTFLOW_SECRETS_DIR=/srv/partflow/secrets|' "$E"
   sed -i 's|^PARTFLOW_BACKUP_DIR=.*|PARTFLOW_BACKUP_DIR=/srv/partflow/backups/database|' "$E"
   sed -i 's|^PARTFLOW_SITE_TIMEZONE=.*|PARTFLOW_SITE_TIMEZONE=<TIMEZONE>|' "$E"
   ip -4 route | grep '172\.30\.250\.'
   ```

4. Create the three secret files, then validate the configuration (NAS guide §6):

   ```sh
   for name in postgres_password partflow_app_password partflow_maintenance_password; do
       openssl rand -base64 32 > "/srv/partflow/secrets/$name"
       chmod 0444 "/srv/partflow/secrets/$name"
   done
   PF="docker compose -f compose.production.yaml --env-file /srv/partflow/env/production.env"
   $PF config --quiet
   ```

5. Build the images and create the database (NAS guide §7):

   ```sh
   PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
   $PF up -d db
   $PF --profile ops run --rm -T db-roles
   $PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"
   ```

   Production starts from a new, empty database. To start from a backup instead, follow the runbook §6 (New-instance
   restore) in place of the last two commands.

6. Start PartFlow with one backend worker and check it on the host (NAS guide §8):

   ```sh
   PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web
   curl -fsS http://127.0.0.1:18080/api/health
   ```

7. Set up HTTPS with Caddy (§5).
8. Create the first Administrator, return to the configured worker count, check the database protection and enroll
   the Scan Stations, exactly as NAS guide §10 describes (the commands are the same with this `PF`).
9. Open the firewall for the approved sources and start using PartFlow. Then set up the backups and the monitoring of
   §8, take a first backup at once and run one restore drill on it (runbook §4); they are recommended and never block
   use:

   ```sh
   deploy/production/backup.sh --kind manual --operator "<your name>" --reason "initial backup" --env-file /srv/partflow/env/production.env
   deploy/production/restore-test.sh --backup <backup name> --operator "<your name>" --env-file /srv/partflow/env/production.env
   ```

## 5. HTTPS with Caddy

Use a dedicated host name such as `partflow.company.example`, and point its DNS record at the VPS only after the check
of §4 step 6 passed. Caddy ends HTTPS and forwards everything to `web` on the loopback address
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Platform proxy requirements). Example `/etc/caddy/Caddyfile`:

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

- `18080` is `PARTFLOW_HTTP_PORT`, and the destination is the literal `127.0.0.1`, never `localhost`.
- Caddy sends `X-Forwarded-For` with the client address and `X-Forwarded-Proto`, and redirects HTTP to HTTPS by
  itself.
- The body limit of 5 MiB and the 300 s timeout are above the limits of `web` (4 MiB and 180 s), so that `web` or the
  application answers with its own message instead of Caddy cutting the request.
- Check that `web` sees the users' real addresses: after a few requests through Caddy, `$PF logs --tail=20 web` must
  show the address of your own computer as the client, not a `172.x.x.x` address. If it does not, the loopback
  connection does not come from the gateway of the `edge` network: set `PARTFLOW_TRUSTED_PROXY` to the address that
  the log shows and run `$PF up -d web` (`../DEPLOYMENT.md` §3.1, Platform proxy requirements).

**Certificate.** A public name gets a certificate from a public certificate authority through ACME automatically,
with no extra directive, and Caddy renews it; ports 80 and 443 must be reachable for that. An internal-only name uses
either `tls /etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (a certificate of the company's certificate
authority; mode 0600, owned by the Caddy user; replace it and run `caddy reload` before it expires) or `tls internal`
(Caddy's own certificate authority, whose root certificate must then be installed on every workstation). If PartFlow
is internal-only, restrict access with the firewall, a VPN or private DNS; a secret address is not a protection.

After Caddy runs, check that `https://partflow.company.example/api/health` answers with `"status":"ok"` and that a
PartFlow address such as `https://partflow.company.example/scan-station` opens directly.

## 6. Installing a new release and rolling back

Use a new checkout for each release, and never install from a branch:

```sh
cd /srv/partflow/releases
git clone https://github.com/CDSemi/part-flow.git <NEW_TAG>
cd <NEW_TAG>
git checkout <NEW_TAG>
deploy/production/release.sh --release <NEW_TAG> --operator "<your name>" --approver "<your name>" --env-file /srv/partflow/env/production.env
ln -sfn /srv/partflow/releases/<NEW_TAG> /srv/partflow/current
```

Run the last command only after `release.sh` exited with 0, so that the schedules run the new release's scripts. For
any other exit code, follow the runbook §5. A rollback follows the runbook §6: "the previous release's checkout" is
`/srv/partflow/releases/<previous tag>`, so run its commands there instead of `git checkout <previous tag>`, and after
a rollback point the `current` link to it again. Keep the previous release's checkout and images until you are sure
you will not roll back. Undoing migrations with `alembic downgrade` is never a rollback.

The runbook writes its commands for the NAS, where the configuration file is `.env.production` in the checkout. On
the VPS, use `/srv/partflow/env/production.env` instead: in `PF` (§3), in every `sed` command that edits the
configuration, as `--env-file /srv/partflow/env/production.env` for every script (`release.sh`, `backup.sh`,
`restore-test.sh`, `smoke.sh`, `check.sh`, `scheduled-reconcile.sh`), and in the `ENV=` and `PF=` lines of the
rollback path 3 scripts after you have saved them to files.

## 7. Moving from the Synology NAS

Follow the runbook §6 (Moving to another host): install the same release on the VPS as in §4 up to the image build,
freeze writes on the NAS, take the final backup, copy and verify it on the VPS, restore it, set up HTTPS, and only
then switch the DNS record. Never let both hosts accept writes.

## 8. Backups, monitoring and alerts

The backup format, its verification, the retention and the restore drill are in the runbook §3 and §4. On a VPS,
provider snapshots may be added as a second layer, but they never replace a tested logical restore. Backups and
monitoring are recommended after go-live and never block use.

**Alert unit.** `/etc/systemd/system/partflow-alert@.service` mails the administrators when another unit fails. It
needs a mail transfer agent on the host (for example msmtp) and journal read access for its user:

```ini
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'journalctl -u %i -n 60 --no-pager | mail -s "PartFlow alert: %i failed on %H" <ops address>'
```

`%i` and `%H` are systemd specifiers; systemd never runs `$(...)`.

**Services and timers.** Each service has `Type=oneshot`, `User=<deployment account>`,
`WorkingDirectory=/srv/partflow/current` and `OnFailure=partflow-alert@%n.service`, and its `ExecStart=` uses an
absolute path, because systemd refuses a relative one:

| Service | `ExecStart=` | Timer |
| --- | --- | --- |
| `partflow-backup.service` | `/srv/partflow/current/deploy/production/backup.sh --kind daily --keep-daily 14 --keep-weekly 8 --operator scheduler --env-file /srv/partflow/env/production.env` | `partflow-backup.timer`: `OnCalendar=*-*-* 02:00:00`, `Persistent=true` |
| `partflow-check.service` | `/srv/partflow/current/deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env` | `partflow-check.timer`: `OnCalendar=*:0/15` |
| `partflow-reconcile.service` | `/srv/partflow/current/deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env` | `partflow-reconcile.timer`: `OnCalendar=*-*-* 04:00:00`, `Persistent=true` |

14 and 8 are example retention values; both options are required. Check every unit with `systemd-analyze verify`
before enabling the timers with `systemctl enable --now <timer>`. Any non-zero exit, including a daily backup that
fails verification, fails its unit and sends the alert. Keep the backup time away from release times, because a
release holds the backup lock.

With cron instead of systemd, set `MAILTO=` and use `--quiet`, because cron mails whenever a job prints something:

```cron
*/15 * * * * cd /srv/partflow/current && deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env --quiet
0 4 * * * cd /srv/partflow/current && deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env --quiet
```

**Encrypted copy outside the VPS (restic).** Set `RESTIC_REPOSITORY` to the object storage location and
`RESTIC_PASSWORD_FILE` to a key file of mode 0400, and keep a copy of that key outside the VPS: without it, the copies
cannot be read. After the backup unit has run:

1. `restic backup --tag database /srv/partflow/backups/database`
2. `restic forget --tag database --keep-daily 14 --keep-weekly 8 --prune`, with the same retention as the backups
3. `restic check`, weekly

A failure of any of these fails its unit and sends the alert. When an archive folder is used later, copy it to its own
restic repository, or under a restic tag that `forget` never targets: archives are never rotated or deleted.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Rewritten with complete installation commands for the VPS folders, one checkout per release with the `current` link for the schedules, the systemd units in one table, and the move from the NAS pointing to the runbook. |
| 2026-11-09 | `b405c6e` | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-09 | `03bd90c` | Added the monitoring units, the alert notifier and the cron alternative. |
| 2026-10-08 | `a330383` | Added production backup automation, the schedule units and the off-VPS restic copy. |
| 2026-10-08 | `1116b07` | Added the database role creation step to the initial deployment. |
| 2026-10-08 | `b820e7a` | Added the release flow and the rollback rule tied to the migrated schema. |
| 2026-10-08 | `d7e86a9` | Added the production Compose stack, the Caddy example and the certificate procedure. |
| 2026-10-06 | `702ab1c` | Added sign-in and first-run setup to the initial deployment. |
| 2026-09-02 | `d2f68de` | First version of the VPS guide with its Vietnamese mirror. |
