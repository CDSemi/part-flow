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
at P16-S2, partially implemented):

- production frontend and backend Dockerfiles/images — implemented (`production`
  stages; the `web` configuration is `frontend/nginx/`);
- production Compose configuration — pending (remainder of P16-S2);
- reverse proxy configuration and certificate procedure — documented (§4 and
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1); verified on the host in P16-S7;
- explicit migration command/job — pending (the Compose `migrate` job is the
  remainder of P16-S2; its command semantics are P16-S3);
- secret/configuration inventory — pending (remainder of P16-S2); the database
  password file contract is in DEPLOYMENT §3.1;
- backup and restore automation — P16-S5;
- health and reconciliation commands — `reconcile` exists (P16-S1); its
  production invocation is pending (remainder of P16-S2, P16-S3);
- logging/monitoring configuration — P16-S6 (the `web` access log is the
  request log in P16-S2);
- release and rollback procedure tied to immutable versions — P16-S3.

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

`18080` stands for the loopback port the production Compose project publishes.
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
  backups/database/
  manifests/
```

The deployment account owns release files. Secrets are readable only by the
required account/service. PostgreSQL data is never inside a Git checkout.

## 6. Initial production deployment

1. Complete all gates in [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Provision and harden the host.
3. Install the exact release files/images; record their digest/commit.
4. Create production secrets and least-privilege database roles. The
   `postgres_password` secret file (one line, [`../DEPLOYMENT.md`](../DEPLOYMENT.md)
   §3.1) arrives with the production Compose file (remainder of P16-S2);
   least-privilege roles are P16-S4, and until then the backend would use the
   bootstrap owner role.
5. Start PostgreSQL privately.
6. Create an empty database in a new volume (production data never starts from
   staging or development data; a restore into production needs an owner
   decision, P16-S5).
7. Run `alembic upgrade head` once from the release backend image.
8. Start backend, frontend, and reverse proxy. On a database with no
   Administrator, start the backend with one worker (`WEB_CONCURRENCY=1`, one
   setup token), complete first-run setup (the setup token is in the backend
   log) before opening access, then restart it with the configured worker
   count. Then enroll each Scan Station device
   (Administration → Scan Stations → `Devices…`).
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

