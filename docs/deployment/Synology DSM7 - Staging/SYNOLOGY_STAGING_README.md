# PartFlow - Synology restricted staging kit

Prepared 2026-09-08 against CDSemi/part-flow source commit:
`d277f8e53a7ca79e0211c211a344dce60e8c7d7f`.

This is a locally authored deployment adaptation, not an upstream PartFlow
production release. No repository changes, DSM installation, image build, live
database migration, or live restore test were performed when preparing this kit.
The YAML structure and shell syntax have been checked locally. Runtime support
must be checked on the actual NAS, especially older models and Docker packages.

## Scope

Restricted company-LAN testing with synthetic data only. Do not publish this
instance through router forwarding, a public tunnel, or QuickConnect. The
current application lacks the production authentication/authorization boundary.
The upstream Dockerfiles still run Uvicorn with reload and the Vite development
server. Several unfinished UI views contain development mock data.

This file is not a production-hardening substitute for Phases 14 and 16.

## Installation layout

Place these five files beside the checkout's `backend/`, `frontend/`, and
`compose.yaml`, for example under `/volume1/docker/partflow/repo/`.
Use the actual storage volume; `volume1` is only an example.

Do not overwrite the upstream README or Compose file. Do not merge
`compose.nas.yaml` with `compose.yaml`: the upstream file publishes extra ports.
Always use `pf.sh`, or exactly the same project name and standalone file.

`pf.sh` prefers `docker compose` and falls back to an installed `docker-compose`.
The format 2.4 declaration is intended to accommodate older Compose parsers;
this is not a guarantee that modern base images support an older NAS kernel or
CPU. A current Compose CLI may warn that the `version` field is obsolete.

## Configuration

Copy `nas.env.example` to `.env`, generate a unique database password using
`openssl rand -hex 32`, and set `POSTGRES_PASSWORD`. A hex password avoids URL
encoding and interpolation issues in the upstream database connection string.
Set `PARTFLOW_BIND_IP` to the reserved NAS LAN address for direct LAN testing,
or keep `127.0.0.1` for a DSM reverse proxy. Do not use `0.0.0.0` for convenience.
Set the factory calendar in `SITE_TIMEZONE` before loading test data.

Keep `.env` mode 600 and the directory accessible only to deployment admins.
Do not reuse secrets from other services. Changing these credentials after the
PostgreSQL volume is initialized does not update the existing database roles.

For an extracted source archive, record its actual immutable source revision:

```sh
printf '%s\n' 'd277f8e53a7ca79e0211c211a344dce60e8c7d7f' > DEPLOYED_SOURCE.txt
```

Do not use this value for a different archive. Git checkouts are detected by
the backup script; deploy only a clean, reviewed checkout.

## First start

Run each command separately. Stop on any failure.

```sh
sudo sh ./pf.sh config -q
sudo sh ./pf.sh build backend
sudo sh ./pf.sh build frontend
sudo sh ./pf.sh up -d db
sudo sh ./pf.sh ps
```

Wait for the database to become healthy, then:

```sh
sudo sh ./pf.sh run --rm --no-deps backend uv run alembic upgrade head
sudo sh ./pf.sh run --rm --no-deps backend uv run alembic current
sudo sh ./pf.sh up -d backend frontend
sudo sh ./pf.sh ps
```

Use `http://<NAS-LAN-IP>:5173/api/health` and the UI from an allowed client.
A healthy endpoint checks database connectivity, not complete schema/business
correctness. Exercise real Administration, Work Orders, Scan Station, and the
Phase 11 boards using designated test data.

## Data and updates

The PostgreSQL named volume is scoped by project name `partflow-staging`.
It is managed by Docker, not stored in the source checkout. The frontend and
backend deliberately have no source bind mounts or dependency volumes: their
source is copied into their images by the existing Dockerfiles. Rebuild images
after updating the source; restarting alone does not update application code.

Do not delete the PostgreSQL volume. Do not run `down -v`, volume pruning,
`alembic downgrade`, or the destructive integration suite against retained data.

For an update: stop frontend/backend, make a fresh verified backup, retain the
old source/release, install the reviewed new source and record its commit,
review configuration and migration changes, build new backend/frontend images,
run a single one-off migration, then start the applications and smoke-test.
Keep the same project name, `.env`, and database volume. Never blindly run
`git pull`, a main/latest auto-updater, or reset the volume to repair credentials.

## Backup and recovery

From an interactive deployment-admin session:

```sh
sh ./backup.sh
```

This writes a custom-format PostgreSQL dump, archive listing, revision manifest,
and SHA-256 checksum file under `../backups/database/`. It checks command
success and a nonempty archive. It does not execute a full restore. Copy all
four files to an encrypted destination outside this NAS. A same-NAS dump is not
off-site protection. Protect `.env` separately in a company secret vault.

For DSM Task Scheduler, a root-owned scheduled task may run:
`sh /volume1/docker/partflow/repo/backup.sh`.
Set an explicit schedule, review failure notifications, and plan retention and
off-NAS replication. The script does not delete old backups automatically.
Backups made by root remain root-readable; perform restore operations through a
root shell when needed, rather than making dumps world-readable.

Restore first to a new, clearly named test database or an isolated stack on the
same PostgreSQL major version. Use `pg_restore --exit-on-error --no-owner
--no-privileges`, verify the migration and representative row counts, then test
the matching application release against the restored copy. Never overwrite the
only retained database during a restore rehearsal.

## Internal HTTPS

Use a hostname controlled by your company with DNS resolving to the NAS LAN IP
and a certificate trusted by clients. Set `PARTFLOW_BIND_IP=127.0.0.1` and set
`PARTFLOW_ALLOWED_HOST` to the exact hostname (no scheme, path, or port).
Recreate the frontend. In DSM Login Portal -> Advanced -> Reverse Proxy, route
HTTPS 443 for that hostname to HTTP 127.0.0.1:5173 and enable WebSocket forwarding.
Assign the matching certificate in DSM Security -> Certificate. Do not disable
Vite host checks or browser certificate validation to hide a configuration error.

Restrict access with tested host and network ACLs. Publishing on a LAN IP does
not restrict which clients can connect, and Docker firewall rules require
verification. On older Docker releases, even loopback-published ports have
known same-L2 exposure caveats; a loopback bind alone is not an access policy.

## Source references

- https://github.com/CDSemi/part-flow/tree/d277f8e53a7ca79e0211c211a344dce60e8c7d7f
- Upstream `compose.yaml`, `backend/Dockerfile`, `frontend/Dockerfile`.
- Upstream `docs/DEPLOYMENT.md`, `docs/deployment/SYNOLOGY_NAS.md`, and
  `docs/deployment/OPERATIONS_RUNBOOK.md`.
- https://www.synology.com/en-us/dsm/feature/container-manager
- https://vite.dev/config/server-options
- https://docs.docker.com/engine/network/port-publishing/
