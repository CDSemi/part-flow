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

Do not deploy production from `compose.yaml`. The release must provide (state
at P16-S2, implemented unless marked pending):

- production frontend and backend Dockerfiles/images — implemented (`production`
  stages; the `web` configuration is `frontend/nginx/`);
- production Compose configuration — implemented (`compose.production.yaml`,
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
- reverse proxy configuration and certificate procedure — documented (§4 and
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1); verified on the host in P16-S7;
- explicit migration command/job — implemented (the Compose `migrate` job,
  profile `ops`, running `python -m app.cli migrate` in one transaction;
  P16-S3, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
- secret/configuration inventory — implemented (`.env.production.example`,
  the `postgres_password` secret file; DEPLOYMENT §3.1);
- backup and restore automation — implemented (P16-S5: `deploy/production/backup.sh`,
  `restore-test.sh`, [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3 and §4);
  executed on a VPS in P16-S7;
- health and reconciliation commands — `reconcile` exists (P16-S1); its
  production invocation is implemented (P16-S2: DEPLOYMENT §3.1 commands); the
  release automation around it is implemented (P16-S3: `deploy/production/release.sh`
  and `smoke.sh`, `revision`, the readiness and liveness endpoints), not yet
  executed on a VPS (P16-S7);
- logging/monitoring configuration — P16-S6 (the `web` access log is the
  request log in P16-S2);
- release and rollback procedure tied to immutable versions — implemented
  (P16-S3: [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5 and §6 and
  `release.sh`; rollback path 3 is implemented in P16-S5, §6 of the runbook).

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

With the production design the host proxy terminates TLS and forwards the whole
origin to the in-stack `web` tier on the loopback address, which serves the
build and routes `/api` ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 lists the
required proxy behavior). Caddy on the host:

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

`18080` stands for `PARTFLOW_HTTP_PORT`, the loopback port the production
Compose project publishes.
Caddy sets `X-Forwarded-For` to the client address and `X-Forwarded-Proto`, and
redirects HTTP to HTTPS automatically. The destination is the literal
`127.0.0.1`, never `localhost`. The 5 MiB body size and 300 s timeout sit above
`web`'s 4 MiB and 180 s so `web`'s or the application's JSON answer wins. If
the loopback connection does not arrive from the `edge` network gateway, set
`PARTFLOW_TRUSTED_PROXY` to the observed address (DEPLOYMENT §3.1); this is a
P16-S7 host check.

**Certificate (Caddy).** The common rules are in DEPLOYMENT §3.1. A public name
gets an automatic ACME certificate and renewal with no extra directive (ports
80 and 443 must be reachable for the challenge). An internal-only name uses
`tls /etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (a company-CA
certificate; files mode 0600 owned by the Caddy user; replaced and `caddy
reload`ed before expiry) or `tls internal` (Caddy's own CA, whose root then must
be distributed to workstations like any internal CA). Check expiry with the
`openssl s_client` command in DEPLOYMENT §3.1. Executed and verified in P16-S7.

## 5. Filesystem layout

Example:

```text
/srv/partflow/
  releases/<immutable-release>/
  current -> releases/<immutable-release>/
  env/production.env
  data/
  backups/database/        = PARTFLOW_BACKUP_DIR (one directory per backup, mode 0700)
```

The backup manifest lives in each backup directory (no separate `manifests/`).
The deployment account owns release files. Secrets are readable only by the
required account/service. PostgreSQL data is never inside a Git checkout.

## 6. Initial production deployment

1. Complete all gates in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Provision and harden the host.
3. Install the exact release files/images; record their digest/commit.
4. Create production secrets and least-privilege database roles. Create the
   three secret files `postgres_password`, `partflow_app_password` and
   `partflow_maintenance_password` in `PARTFLOW_SECRETS_DIR` (one line each,
   [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1) and `.env.production` from
   `.env.production.example`, before any `$PF` command that starts `backend`
   or an ops service. `PF` below is `docker compose -f compose.production.yaml
   --env-file .env.production`. Build both images with
   `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`.
   Create the backup directory (`install -d -m 0700 /srv/partflow/backups/database`)
   and set `PARTFLOW_BACKUP_DIR` to it in `.env.production` (unquoted) first:
   every `$PF` command needs the key.
5. Start PostgreSQL privately: `$PF up -d db`, then create the database roles:
   `$PF --profile ops run --rm -T db-roles` (P16-S4; the backend connects as
   `partflow_app`, never as the owner).
6. Create an empty database in a new volume (production data never starts from
   staging or development data; a restore into production follows
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 path 3 or §8 host
   failure, with the owner's approval recorded before `backend` starts).
7. Run the migration once from the release backend image:
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`;
   it also applies the grants.
8. Start backend, `web` and the host reverse proxy (§4). On a database with no
   Administrator, start the backend with one worker
   (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web`, one setup token),
   complete first-run setup (the setup token is in the backend log:
   `$PF logs backend | grep "Setup token"`) before opening access, then
   restart it with the configured worker count (`$PF up -d backend`). Then enroll each Scan Station device
   (Administration → Scan Stations → `Devices…`).
9. Run the runbook smoke and reconciliation checks through HTTPS; reconcile
   check (h) must report `pass`.
10. Enable monitoring and the backup schedule (the systemd timer of §8), then run
    a backup immediately:
    `deploy/production/backup.sh --kind manual --operator "<name>" --reason "initial backup"`.
11. Perform and time an isolated restore before pilot data is accepted:
    `deploy/production/restore-test.sh --backup <that backup> --operator "<name>"`.
12. Open only the approved network sources and begin the controlled pilot.

To convert a rehearsal stack installed before P16-S4, in this order: (1) create
`partflow_app_password` and `partflow_maintenance_password` before any `$PF`
command of the new Compose file that runs `backend` or `db-roles`; (2) build both
images with `PARTFLOW_COMMIT` (`PARTFLOW_RELEASE=<tag> PARTFLOW_COMMIT=$(git rev-parse HEAD)
$PF -f compose.production.build.yaml build backend web`), never only `backend`;
(3) `PARTFLOW_RELEASE=<tag> $PF --profile ops run --rm -T db-roles`; (4)
`PARTFLOW_RELEASE=<tag> $PF --profile ops run --rm -T db-roles apply-grants`
(the tag prefix runs the new image: `.env.production` still names the running
release); (5) `deploy/production/release.sh`
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Converting a stack installed
before P16-S4). A rehearsal stack installed before P16-S5 first creates the
backup directory and sets `PARTFLOW_BACKUP_DIR` **before any `$PF` command with
the P16-S5 checkout**, then runs `release.sh` (Converting a stack installed
before P16-S5, same section).

## 7. Releases and rollback

Use immutable directories/images. Build/pull the new release before stopping the
old one. Back up before migration. Never deploy directly from a mutable `main`
checkout.

`release.sh` takes the verified pre-release backup inside the write freeze.
Application rollback is allowed only when the previous code is compatible with
the migrated schema. If not, restore the pre-migration database and matching
application release together (rollback path 3 of the runbook: a new database in
the same instance, with the owner's approval recorded). Alembic downgrade is not a generic rollback:
PartFlow migrations may protect immutable history by refusing destructive
downgrades.

Follow [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 8. Backup strategy

Use PostgreSQL logical dumps as the portable baseline and optionally add
provider volume snapshots as a second layer. Snapshots never replace tested
logical restore. The artifact, its verification, the retention and the restore
drill are in [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3 and §4
(implemented in P16-S5; the units and tasks below are installed and executed on
the VPS in P16-S7):

- scheduled custom-format `pg_dump` (`backup.sh`, one directory per backup);
- checksum and manifest containing release commit and Alembic revision;
- encryption in transit and at rest;
- off-VPS copy with retention and failure alert;
- periodic restore to an isolated database;
- documented RPO/RTO measured from actual runs.

**Schedule (systemd).** `partflow-backup.service`, `Type=oneshot`,
`User=<deploy account>`, `WorkingDirectory=/srv/partflow/current`,
`ExecStart=/srv/partflow/current/deploy/production/backup.sh --kind daily --keep-daily 14 --keep-weekly 8 --operator scheduler --env-file /srv/partflow/env/production.env`
(14 and 8 are placeholders: the owner sets the real values, and the options are
required), `OnFailure=` a unit that mails the administrators; and
`partflow-backup.timer` with `OnCalendar=*-*-* 02:00:00` and `Persistent=true`.
`ExecStart=` needs the absolute path (`WorkingDirectory=` does not resolve a
relative one, and systemd refuses the unit); check both units with
`systemd-analyze verify partflow-backup.service partflow-backup.timer`.
A non-zero exit, a daily that fails verification included, fails the unit. Keep
the schedule outside maintenance windows (a release holds the backup lock).

**Off-VPS copy (restic).** `RESTIC_REPOSITORY` points at object storage, and
`RESTIC_PASSWORD_FILE` at a key file of mode 0400 whose custody is kept off the
VPS (losing it loses the copies). After the backup unit:
`restic backup --tag database /srv/partflow/backups/database`, then
`restic forget --tag database --keep-daily 14 --keep-weekly 8 --prune` (the
retention matches `backup-rotate`), and `restic check` weekly; a failure fails its
unit (`OnFailure=`). The archive directory (Phase 16 slice S8) goes to its own
repository or tag that `forget` never targets: archives are never rotated or
pruned.

## 9. Moving from Synology

Use the backup and restore cutover in `SYNOLOGY_NAS.md` §10 (the same backup
artifact and the new-instance restore of the runbook §6). Keep source and target at
the same release, enforce a write freeze, verify checksums and reconciliation,
then switch DNS. Never run two writable production instances against divergent
databases.

