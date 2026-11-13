# PartFlow Deployment Guide

> **Language:** English is the source of truth. [Tiếng Việt](./DEPLOYMENT.vi.md).

This guide is the starting point for installing and running PartFlow in production. Read §1 and §2 first: they
explain what you are installing, which document to follow for each task, and the safety rules that every procedure
relies on. The rest of this guide is reference material; the procedures point to it when you need a detail.

## 1. Start here

### 1.1 What you are installing

In production, PartFlow is a set of Docker containers defined in `compose.production.yaml` (Compose project
`partflow-production`). The images are built on the host itself from a release tag of this repository. Three
containers run all the time:

- `db`: PostgreSQL, which keeps every PartFlow record in the Docker volume `partflow-production_postgres_data`;
- `backend`: the PartFlow application programming interface (API), a FastAPI application;
- `web`: nginx, which serves the PartFlow screens and forwards every `/api` request to `backend`.

Users never connect to these containers directly. They open `https://<PartFlow host name>`. The reverse proxy of the
host (the DiskStation Manager (DSM) reverse proxy on a Synology NAS, or Caddy on a Linux server) ends the HTTPS
connection and forwards the request to `web` on the host's own loopback address `127.0.0.1`, which no other computer
can reach:

```text
Browser or Scan Station
        │  HTTPS, port 443
        ▼
Reverse proxy of the host (DSM reverse proxy, or Caddy on a VPS)
        │  HTTP to 127.0.0.1:18080, reachable only from the host itself
        ▼
web (nginx): PartFlow screens, request size limits, sign-in rate limits
        │  /api
        ▼
backend (FastAPI) ── private container network ── db (PostgreSQL)
```

Besides the containers, an installation has four items on the host that you create and look after:

| Item | What it holds | Example on the NAS |
| --- | --- | --- |
| Release checkout | A git clone of this repository with the release tag checked out. Every command runs from its root folder. | `/volume1/docker/partflow/repo` |
| `.env.production` | The configuration, without any password, in the root folder of the release checkout. | `/volume1/docker/partflow/repo/.env.production` |
| Secrets folder | The three database password files. | `/volume1/docker/partflow/secrets` |
| Backup folder | One sub-folder per database backup. | `/volume1/docker/partflow/backups` |

The scripts in `deploy/production/` do the risky work for you: `release.sh` installs a new release, `backup.sh` takes
a backup, `restore-test.sh` proves that a backup can be restored, and `check.sh` and `scheduled-reconcile.sh` watch
the installation.

### 1.2 Which document to follow

| I want to … | Follow |
| --- | --- |
| Install PartFlow on the company Synology NAS for the first time | [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §1 to §10, then §11 when convenient |
| Let people reach PartFlow from the Internet (optional) | [`deployment/SYNOLOGY_PUBLIC_HTTPS.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.md), at the HTTPS step of the NAS guide (§9) |
| Install a new release, take or test a backup, check the installation, roll back, or handle a problem | [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) |
| Run PartFlow on a Linux virtual private server (VPS) instead of the NAS | [`deployment/VPS.md`](./deployment/VPS.md) |
| Decide whether shared web hosting could work | [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md) |
| Look up a setting, a limit or what a container does | §3.1 of this guide |
| Name and publish a release (developers) | §10 of this guide |

The Deployment Admin tool `pf` and its guide [`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md) manage
separate staging instances. They are not part of the production installation, and no step above needs them.

### 1.3 What is supported

| Use | Status |
| --- | --- |
| Developer workstation | Supported with the development stack `compose.yaml` (root [`README.md`](../README.md)). Try a new release here before you install it in production. |
| Production on the company Synology NAS | Supported. This is the production host in use. |
| Production on a Linux VPS | Supported as the alternative host. |
| Access from the company network or a virtual private network (VPN) | Supported (NAS guide §9, option A). |
| Access from the Internet | Supported through Cloudflare as [`deployment/SYNOLOGY_PUBLIC_HTTPS.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.md) describes. Other designs are not documented. |
| A second PartFlow stack (staging) on the production host | Not supported: `release.sh` refuses to run while a Compose project `partflow-staging` exists on the same Docker daemon (§6). |
| Shared web hosting | Not supported unless a provider proves every requirement of `deployment/SHARED_HOSTING.md`. |

### 1.4 Status

The production stack, its scripts and the release, backup and restore procedures are complete. Their automated tests
run in a Linux container and on Docker Desktop. The platform guides follow those scripts step by step; when a step
behaves differently on a real host, correct the guide in the same change as the fix.

By the owner's decision of 2026-11-09, PartFlow goes into real use as soon as the production stack is installed: there
is no pilot phase and no list of release gates, and problems are fixed in later releases. Only the first list of §5 is
required. Backups and monitoring are strongly recommended and never block use.

## 2. Terms, conventions and safety rules

### 2.1 Terms

| Term | Meaning |
| --- | --- |
| Release | One version of PartFlow: a git tag such as `v1.0.0` (§10.2) and the commit it points to. Both images of a release are built from that commit and carry its tag. |
| Release checkout | The git clone with the release tag checked out. Every command runs from its root folder. |
| Operating account | The one account that runs every PartFlow command, `release.sh` and every scheduled task, and that owns the backup folder. On a Synology NAS this is `root`, because Docker on DSM needs it. |
| `PF` | A shell variable that shortens the Compose command of the production stack (below). It is not the Deployment Admin tool `pf`. |
| Ops job | A one-shot container (`migrate`, `db-roles`, `backup-tools` or `status`) that runs only when started with `$PF --profile ops run`. |
| Write freeze | `backend` stopped on purpose, so that nothing can be written, for example during a database migration. `web` keeps serving the screens, which show OFFLINE and block every change. |
| First-run setup | Creating the first Administrator in the browser with a one-time setup token that `backend` writes to its log. |
| Reconciliation | The read-only consistency check `python -m app.cli reconcile`, with the checks (a) to (j) of the runbook §7. It never changes data. |
| Records directory | The folder `~/partflow-deployments` of the operating account, with one sub-folder per release, restore drill, rollback or restore. |
| Owner | The person who approves releases, restores and repairs. Today the owner is also the operator; one person may give the same name as operator and approver. |

Every production command uses the shell variable `PF`. Set it in every new terminal session, in the root folder of the
release checkout:

```sh
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

`$PF ps` then stands for `docker compose -f compose.production.yaml --env-file .env.production ps`.

### 2.2 Safety rules

These rules protect the production data. Every procedure follows them; never break one to save time.

1. **Never run production from `compose.yaml`.** That file is the development stack: it runs development servers that
   reload automatically, publishes the database port, uses one database role for everything and has development
   defaults. Production uses `compose.production.yaml` only.
2. **Never run `$PF down -v`, and never delete a volume of the `partflow-production` project.** Either deletes the
   production database. `$PF down` without `-v` is the only safe way to stop everything.
3. **Never delete the previous release's images** (for example with `docker image prune -a`) while you might still
   roll back: rolling back needs them.
4. **Production starts from a new, empty database.** Never attach, copy or restore development or test data into it,
   and never point two installations at one database.
5. **Never edit PartFlow data directly in the database.** Movement history is append-only by design; corrections go
   through PartFlow's own correction workflows, and every repair is the owner's decision.
6. **Never repeat an uncertain production command under a new identity.** A command whose answer was lost keeps its
   original `device_event_id` and is retried only with it (runbook §2 and §8).
7. **Never run `alembic downgrade` on production.** A rollback follows the decision tree of the runbook §6.
8. **Treat backups, secret files and reconciliation reports as secret.** They contain password hashes, session and
   device token digests, and badge values.

## 3. Production stack reference

This section is reference. The installation steps are in the platform guides ([`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md),
[`deployment/VPS.md`](./deployment/VPS.md)) and the operating procedures in
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md); they point here when you need a detail.

### 3.1 Production stack

#### Files

| File | Purpose |
| --- | --- |
| `compose.production.yaml` | The production stack: the Compose project `partflow-production` with its services, networks, volume and secret files. It has no build section. |
| `compose.production.build.yaml` | Build-only companion that adds the two image builds. Used only with `build`. |
| `.env.production.example` | Template of the configuration file `.env.production`. |
| `backend/Dockerfile`, `frontend/Dockerfile` | Each holds a `production` stage that only the production build uses; `frontend/nginx/` holds the configuration of `web`. |
| `deploy/production/` | The scripts listed at the end of this section. Each prints its options with `--help`. |

#### Services

| Service | What it does | Details |
| --- | --- | --- |
| `db` | PostgreSQL 16 (`postgres:16.14`, Debian variant) | Network `internal` only; no published port. Data in the volume `partflow-production_postgres_data`. Never an `-alpine` image: text sorting (collation) and the identity check "Canonical identity under the running interpreter and database" (reconcile check (j)) depend on the GNU C Library (glibc). Logs errors without detail lines (`log_error_verbosity=terse`). 60 s stop grace period. |
| `backend` | The API, image `partflow/backend:<release>` | Networks `internal` and `edge`. Connects as the database role `partflow_app` and mounts only its password file. `SESSION_COOKIE_SECURE`, `ENFORCE_CLIENT_RELEASE` and `DATABASE_ROLES_REQUIRED` are fixed to `true`. Worker count from `PARTFLOW_BACKEND_WORKERS`; forwarded headers trusted from the `edge` subnet only. 200 s stop grace period, above the longest request time of `web` (180 s), so that a running import can finish. `restart: unless-stopped`, never `on-failure`, because with several workers a configuration refusal exits with code 0. |
| `web` | nginx, image `partflow/web:<release>` | Network `edge`. The only published port: `127.0.0.1:${PARTFLOW_HTTP_PORT}` to container port 80; there is deliberately no setting for the bind address. No `depends_on`, so it keeps serving the screens while `backend` is stopped. 200 s stop grace period. |
| `migrate` | Ops job: `python -m app.cli migrate` | Applies database migrations and grants in one transaction. Needs a backup option (Migrations, below). Mounts the backup folder read-only at `/backups`. |
| `db-roles` | Ops job: `python -m app.cli provision-roles` (default) or `apply-grants` | Creates or repairs the database roles and applies the grants (Database roles, below). Connects as the owner role and mounts the three secret files. |
| `backup-tools` | Ops job: `python -m app.cli backup-manifest`, `backup-verify` or `backup-rotate` | Used by `backup.sh`. No network, no database and no secret; mounts only the backup folder at `/backups`. |
| `status` | Ops job: `python -m app.cli status` | A read-only report as `partflow_app`, with the backup folder mounted read-only. Used by `check.sh`. |

Every service has a restart policy, memory and processor limits from `.env.production`, and log rotation (`json-file`,
10 MiB per file, 5 files). Every long-running service has a health check; the `backend` health check calls the
liveness route, so a schema mismatch never marks the container unhealthy. The network `internal` has no route outside
the host. Run the ops jobs that read or write the backup folder with `--user "$(id -u):$(id -g)"`, because that folder
has mode 0700 and belongs to the operating account.

#### Configuration file

`.env.production` is a copy of `.env.production.example` in the root folder of the release checkout. Git ignores it,
its mode is 600, and it holds no password. Write every value unquoted. A static test checks that the keys of the
example file are exactly the `${NAME}` references of the two production Compose files.

| Key | Meaning | Required or default |
| --- | --- | --- |
| `PARTFLOW_RELEASE` | Tag of the release that is **running**. `release.sh` changes it at the switch to a new release. | required |
| `PARTFLOW_COMMIT` | Full commit of the release being built. Leave it empty: the build command sets it in the shell. | empty |
| `PARTFLOW_ACCEPT_SCHEMA_REVISION` | Rollback path 2 only (runbook §6): the one database revision the running release may serve. `release.sh` clears it. | empty |
| `PARTFLOW_SECRETS_DIR` | Absolute path of the secrets folder, outside the release checkout (folder mode 0700, file mode 0444). | required |
| `PARTFLOW_BACKUP_DIR` | Absolute path of the production backup folder: outside the release checkout, the secrets folder and any archive folder, never shared with another installation or a restore drill, mode 0700, owned by the operating account. It must exist before the first `$PF` command, because **every** `$PF` command needs it. | required |
| `PARTFLOW_SITE_TIMEZONE` | The factory's time zone, as an IANA name such as `America/Los_Angeles` (§6). | required |
| `PARTFLOW_HTTP_PORT` | Loopback port that the reverse proxy connects to. | `18080` |
| `POSTGRES_USER`, `POSTGRES_DB` | Name of the database owner role and of the database. | `partflow_owner`, `partflow` |
| `PARTFLOW_BACKEND_WORKERS` | Number of `backend` worker processes. | `2` |
| `PARTFLOW_EDGE_SUBNET` | Subnet of the `edge` network, also the addresses `backend` trusts for forwarded headers. It must not be used anywhere else on the host or the company network. | `172.30.250.0/24` |
| `PARTFLOW_TRUSTED_PROXY` | One IPv4 address that `web` trusts for forwarded headers. Empty means detected automatically (Platform proxy requirements, below). | empty |
| `PARTFLOW_{DB,BACKEND,WEB,OPS}_{MEMORY,CPUS}` | Memory and processor limits of each service; the ops jobs share the `OPS` values. `0` as a `CPUS` value means no processor limit, which a host whose kernel has no processor quota support needs (NAS guide §3). | `1g`/`1.0`, `1g`/`2.0`, `128m`/`0.5`, `512m`/`1.0` |

These settings are fixed in `compose.production.yaml` and cannot be configured: `SESSION_COOKIE_SECURE=true`,
`ENFORCE_CLIENT_RELEASE=true`, `DATABASE_ROLES_REQUIRED=true` (on `backend` and `migrate`), `DATABASE_HOST=db`,
`DATABASE_PORT=5432`, the secret file paths and the loopback bind of `web`.

The backend reads its database connection in exactly one of two forms: `DATABASE_URL` (development, tests, continuous
integration (CI) and staging), or `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` and `DATABASE_PASSWORD_FILE` with an
optional `DATABASE_PORT`, which the production stack uses. Both forms together, or neither complete, are refused at
startup. Because the application builds the connection itself, a password needs no special encoding, and no error
message repeats an input value.

#### Secret files

No password is a configuration value and none has a default. The passwords are three files in the secrets folder,
mounted into the containers under `/run/secrets`:

| File | Used by | Rules |
| --- | --- | --- |
| `postgres_password` | the owner role: `db`, `migrate`, `db-roles` | One line. `db` reads it **only when it creates a new, empty database volume**; `migrate` and `db-roles` read it every time they run. `backend` never mounts it. |
| `partflow_app_password` | `backend`, `status`, `db-roles` | One line of 16 to 128 printable ASCII characters without spaces. |
| `partflow_maintenance_password` | `db-roles` | Same rules, and different from `partflow_app_password`. |

Create the three files before the first command that starts `backend` or an ops job. If a file is missing, Compose
creates an empty **folder** with that name instead; remove that folder first (`rmdir <path>`), then create the file.
`release.sh` refuses to run, changing nothing, while any of the three files is missing, empty or not a regular file.
To create them, set `PARTFLOW_SECRETS_DIR` in the shell to the same absolute path as in `.env.production` and run:

```sh
mkdir -p "$PARTFLOW_SECRETS_DIR" && chmod 0700 "$PARTFLOW_SECRETS_DIR"
for name in postgres_password partflow_app_password partflow_maintenance_password; do
    openssl rand -base64 32 > "$PARTFLOW_SECRETS_DIR/$name"
    chmod 0444 "$PARTFLOW_SECRETS_DIR/$name"
done
```

Each file holds exactly one line (a final line break is ignored), because PostgreSQL takes a new role's password from
the first line only. A file that has several lines, is empty, cannot be read or is not UTF-8 is refused with a message
that names the file, never its content. Changing a password later is a routine task (runbook §9).

#### Images and release identity

Images are built on the host from the checked-out release and never pulled (`pull_policy: never`). Always build both
images together, with the release tag and its full commit in the shell:

```sh
PARTFLOW_RELEASE=<tag> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
```

At the first installation the tag is already in `.env.production`, so `PARTFLOW_RELEASE=<tag>` can be left out.
Because `compose.production.yaml` has no build section, `up` or `run` with a tag whose images are missing fails with
`No such image` instead of building the current checkout under that tag. An existing tag is never rebuilt.

- **`backend` image:** Python 3.12 slim with the locked production dependencies, without tests, `.env` or reload
  server. It runs as user `10001:10001` with
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --log-config app/core/logging.production.json`
  and never migrates the database when it starts.
- **`web` image:** the pinned official `nginx:1.30.5-alpine` with the frontend built by `npm run build`, which also
  checks that no development preview data is in the build.
- **Release identity.** The build refuses a tag that is not 1 to 64 letters, digits, `.`, `_` or `-` (or is
  `development`) and a commit that is not 40 lowercase hexadecimal characters. Both images store the tag and the commit
  as the labels `org.opencontainers.image.version` and `org.opencontainers.image.revision`; the `backend` image also
  as `RELEASE_TAG` and `RELEASE_COMMIT` (outside a release image they are `development` and empty). The `web` image
  bakes the tag into the screens and the `partflow-release` meta tag. Every page sends the tag of its build in the
  request header `X-PartFlow-Release`, which the backend compares exactly with its own.

#### Health routes and the write gate

The backend has two health routes:

- **Liveness, `GET /api/health/live`**, answers `{"status":"live","service","release","commit"}` without touching the
  database. Every container health check uses it.
- **Readiness, `GET /api/health`**, also reads the database revision. Its keys are `status`, `service`, `database`,
  `release`, `commit`, `schema`, `expected_revision`, `database_revision` and `accepted_revision`. It answers `200`
  with `"status":"ok"` only when `schema` is `current` or `accepted`, `503 not_ready` on a `mismatch`, and
  `503 unavailable` with `"schema":"unknown"` when the database cannot be reached.

| `schema` | Meaning |
| --- | --- |
| `current` | The database revision equals the single Alembic head of the running image. |
| `accepted` | The database is at the one revision named by `PARTFLOW_ACCEPT_SCHEMA_REVISION`, which the running image does not know (rollback path 2, runbook §6). A revision the image knows is ignored with a warning, and the `revision` command then reports `"override_ignored": true`. |
| `mismatch` | Any other database revision. |
| `unknown` | The database cannot be read. |

**The write gate.** The backend refuses every `POST`, `PUT`, `PATCH` and `DELETE`, on any path, before it does
anything else, in two cases. Both refusals write nothing and carry `Cache-Control: no-store`; the screens show them as
`GUI_DESIGN.md` §3 rule 13 describes.

| Answer | When | Meaning for the user |
| --- | --- | --- |
| `409` with `release_mismatch: true` | The request's `X-PartFlow-Release` is missing or differs from the running release: the page was loaded from an older release. | Definitely refused; nothing was recorded. The page must be reloaded. |
| `503` with `not_ready: true` | The schema is a `mismatch`, or readiness cannot be confirmed. | Unknown outcome. |

Reads and the health routes are never gated. Release enforcement (`ENFORCE_CLIENT_RELEASE`) is on in production and
off in development and tests, and the backend refuses to start with enforcement on under the tag `development`. The
one write on a read request, the last-seen time of a Scan Station device, holds no production data and is accepted
during a mismatch.

#### Migrations and the `revision` command

`python -m app.cli migrate` (the `migrate` job) applies the pending database migrations on one connection in one
transaction, applies the database grants in the same transaction, and prints a JSON report whose `result` is
`upgraded`, `already_current`, `refused`, `failed` or `outcome_unknown`.

- It needs exactly one of `--pre-release-backup NAME` (a `backup.sh` backup, verified before the migration; with a
  pending migration also `--backup-not-before UTC`, the time the write freeze started) or `--no-backup-reason TEXT`
  (first installation and rehearsals only; the reason is recorded). The rules that a pre-release backup must meet are
  in the runbook §5.
- It accepts `--lock-timeout SECONDS` (1 to 600, default 30) and `--backup-dir` (default `/backups`).
- It refuses, changing nothing: a migration with non-transactional schema changes, a database revision the release
  does not know, a second `migrate` at the same time, and a connected `backend` (a best-effort check: stop `backend`
  yourself).
- A database-role or grant refusal (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`,
  `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) ends as `refused` with exit code 1, and the
  upgrade of the same run is rolled back.

The backend never migrates by itself. `python -m app.cli revision` is read-only: it prints the release identity, the
expected and the database revisions, the pending revisions and the readiness the backend would report, and exits with
0 only when the schema is `current`.

#### Database roles and grants

The production database has three roles:

| Role | Used by | Notes |
| --- | --- | --- |
| Owner (`POSTGRES_USER`, default `partflow_owner`) | `db`, `migrate`, `db-roles`, backups | The PostgreSQL superuser created with the database; it owns every object. Its name must differ from the two roles below. |
| `partflow_app` | `backend` (the API and its commands `reconcile`, `revision` and the recovery commands) and `status` | Holds only the privileges in the next table. |
| `partflow_maintenance` | no service yet | Reserved for the planned archival maintenance; holds `SELECT` on a named list of tables only. |

| Table class | `partflow_app` privileges | Tables |
| --- | --- | --- |
| append-only | `SELECT`, `INSERT` | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| guarded update | `SELECT`, `INSERT`, `UPDATE` | `worker_sessions` (a row trigger limits the columns) |
| no update | `SELECT`, `INSERT`, `DELETE` | `assigned_route_steps` |
| read only | `SELECT` | `alembic_version` |
| ordinary | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | every other table |

- `provision-roles` (the default of `db-roles`) creates or repairs `partflow_app` and `partflow_maintenance` as
  `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, with no membership, no per-role setting and no
  owned object, and sets their passwords (SCRAM-SHA-256) from the secret files in one transaction. It is safe to repeat.
- `apply-grants` derives every grant from the table classification in the code and applies it in one transaction. It
  refuses unless the database is at the release's Alembic head.
- Every `migrate` applies the grants after the upgrade in the same run, so a release never leaves a new table without
  grants. An unclassified table, a role with a forbidden attribute or membership, a role that owns objects, or a
  privilege granted by a role that PartFlow does not manage rolls the whole run back.
- With `DATABASE_ROLES_REQUIRED=true` (production), missing roles are refused as `roles_not_provisioned`. Without it
  (development, tests, staging), the grants report says `not_provisioned` and nothing is granted.
- Grants are never restored from a backup: after a restore, run `apply-grants`.
- No role holds `TRUNCATE`, a sequence privilege, a grant option, or `CREATE` on the database or the schema `public`,
  and the privileges of `PUBLIC` and the default privileges are removed. The full classification is
  [`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md) §17.

The database triggers that refuse a forbidden write are the first layer of protection; the missing privileges are the
second. A `permission denied` error (SQLSTATE 42501) in the backend log is therefore an incident (runbook §8), never a
reason to grant more. The guard check "Append-only guards and database-role privileges intact" (reconcile check (h))
verifies this protection. A superuser who disables a trigger is outside what PartFlow can detect; this limit is
accepted.

#### Backend processes and first-run setup

`PARTFLOW_BACKEND_WORKERS` sets the number of worker processes of `backend` (Compose passes it as `WEB_CONCURRENCY`, so
the value of your own shell variable `WEB_CONCURRENCY` has no effect).

- **First-run setup needs one worker.** Every worker has its own one-time setup token. With two workers, the browser
  may reach the worker whose token you did not copy and gets `403 setup_token_invalid` (nothing is written). Start
  `backend` with `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, read the token with
  `$PF logs backend | grep "Setup token"`, complete setup, then run `$PF up -d backend` to return to the configured
  count. Every start of `backend` writes a new token, so always use the newest one. The setup token is the only secret
  ever written to the backend log: complete setup before anyone else can reach PartFlow, and restrict access to the
  log until then.
- **A configuration error exits with code 0** when there are several workers. Read the log and the restart count, not
  the exit code.
- **Stopping a backend with several workers does not refuse new requests at once.** The main process keeps the port
  open until its last worker has exited, which takes up to the 200 s stop grace period while an import finishes. A
  request sent meanwhile is accepted but never answered: it ends as a 504 from `web` after 60 s, or a 502. The backend
  never ran it, but the user must treat it as an unknown outcome. Therefore start a write freeze when no import is
  running.

#### Platform proxy requirements

The reverse proxy of the host (the DSM reverse proxy, or Caddy on a VPS) must:

- accept HTTPS with a certificate that every workstation and Scan Station trusts, and never forward plain HTTP to
  PartFlow (at most, redirect it to HTTPS);
- forward to `http://127.0.0.1:<PARTFLOW_HTTP_PORT>` with the literal address `127.0.0.1`, never `localhost`, because
  `localhost` can resolve to the IPv6 address `::1` first, where `web` does not listen;
- send the `Host` header, `X-Forwarded-For` with the client address appended last, and `X-Forwarded-Proto: https`;
- accept request bodies of at least 5 MiB and wait at least 300 s for an answer (send and read timeouts), so that the
  JSON answer of `web` or of the application arrives first; otherwise picture uploads and Work Order imports fail;
- never log cookies or the headers `X-PartFlow-Station-Device` and `X-PartFlow-CSRF`;
- admit only the intended networks, together with the host firewall.

**Real client address.** `web` trusts forwarded headers from exactly one hop. By default, that hop is detected when
the container starts: it is the container's default gateway on the `edge` network, which is where connections to the
host's loopback address come from. Set `PARTFLOW_TRUSTED_PROXY` (one IPv4 address) only when the proxy arrives from
another address; if no address can be found, `web` refuses to start. `web` takes the **last** `X-Forwarded-For`
address as the client, sends only that address to the backend, and honours `X-Forwarded-Proto` only from the same hop.
When the proxy cannot supply the client address, all users share one rate-limit bucket (Request limits, below). `web`
holds no certificate.

#### Certificate requirements

The certificate names the PartFlow host name as a subject alternative name (SAN). It comes from one of:

- a public certificate authority (CA), for example Let's Encrypt through the Automatic Certificate Management
  Environment (ACME) protocol, for a public DNS name;
- the company's internal CA;
- a private CA created for this purpose, such as the one DSM creates (NAS guide §9.1);
- Cloudflare's Origin CA, only behind the Cloudflare proxy ([`deployment/SYNOLOGY_PUBLIC_HTTPS.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.md)).

Never use a bare self-signed certificate, and never let users click past a browser warning. The root certificate of an
internal or private CA is installed as trusted on every workstation and Scan Station device, through the company's
device management or by hand. Whoever installed the certificate renews it; `check.sh` warns 21 days before the
certificate it sees expires. To read the expiry date from any computer:

```sh
openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -subject -enddate
```

#### Request limits, rate limits and answers from `web`

| Route | Largest request body | Longest wait for `backend` |
| --- | --- | --- |
| Every other `/api` route | 1 MiB | 60 s |
| `PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image` | 4 MiB | 60 s |
| `POST /api/work-orders/import/preview` | 4 MiB | 60 s |
| `POST /api/work-orders/import` | 4 MiB | 180 s |

The 4 MiB limit is above the application's own limits (2 MiB pictures, 1 MiB import files), so the application
answers a plausible oversized file itself. The largest allowed import, 2,000 Work Orders, takes about half a minute.
`web` never retries a request, so one user request is at most one backend execution.

**Rate limits, per client address.** `POST /api/session` (sign-in) allows 10 per minute with a burst of 5.
`PUT /api/session/password`, `POST /api/setup/administrator` and `PUT /api/users/{user_id}/password` allow 5 per minute
with a burst of 4. Reads and sign-out are never limited. A 429 from `web` never reaches the application, so it never
counts as a failed sign-in and never locks an account. Device enrollment codes are not rate limited (50 bits of
entropy, valid for 15 minutes).

`web` answers only for conditions it detects itself; every answer of the application passes through unchanged:

| Status | When | Message (`detail`) | Meaning |
| --- | --- | --- | --- |
| 413 | The body is larger than the route allows. | `This request is too large for PartFlow. Nothing was changed.` (`request_too_large: true`) | Definitely refused. |
| 429 | Rate limit exceeded; sends `Retry-After: 60`. | `Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.` (`rate_limited: true`) | Definitely refused. |
| 502 | `backend` cannot be reached, or closed the connection before answering. | `The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | Unknown outcome for a change. |
| 504 | `backend` did not answer in time. | `The PartFlow server did not answer in time. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | Unknown outcome for a change. |

A Scan Station command that may have been saved is shown as unknown and retried with its original `device_event_id`
(runbook §2). The screens show a built-in message for a 413 or 429 without a JSON body, for example one from the
reverse proxy. `web` adds no cross-origin resource sharing (CORS) headers.

**Caching and security headers.** `index.html` and every route of the single-page application are `no-cache`; the
files under `/assets/` are cached for one year, and a missing asset is a plain 404. Every response carries
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and a `Content-Security-Policy` that allows only the
PartFlow origin for scripts, styles, connections and frames, plus `blob:` and `data:` images for the local picture
previews, and no inline script. A feature that needs another source changes this policy in the same change. HTTP
Strict Transport Security (HSTS) is the reverse proxy's job; `web` does not set it.

#### Logs

- **Request id.** Every response carries an id in `X-Request-ID`. A client value of 1 to 64 characters of
  `A-Za-z0-9._-` is kept; otherwise one is generated. `web` passes it to the backend, and every log record of that
  request carries it as `request_id`.
- **`web` log.** One JSON record per request: client address, method, path without query string, status, bytes,
  duration, upstream status, user agent and `request_id`. Health probes are not logged.
- **Backend log.** One JSON line per record on standard error: `ts`, `level`, `logger`, `message`, `request_id`, then
  the record's own fields. A traceback never contains a database error message, so no statement or value reaches the
  log.
- **Application record (`app.access`).** Written at `INFO` for a change, for a designed refusal (`not_ready`,
  `release_mismatch`, `password_check_busy`, validation, authorization) and for a read that took one second or more
  (`"slow":true`); at `ERROR` for a failure; at `DEBUG` for a health poll or a fast read. Its fields are `event`,
  `method`, `route`, `status`, `duration_ms`, `client`, `outcome` (`ok`, `created`, `replayed`, `refused` or
  `error`), `slow`, `refusal` (`type`, `code`, `message`) and `context`: the Part Number, QuantityFlow, quantity, Area,
  Operation, Machine, Work Order, Work Order Demands, `device_event_id`, Scan Station, `user_id` and `worker_id` that
  the request named. `worker_id` appears only once the command reached identity resolution; the Worker of a replayed
  command is in its Movement.
- **Never logged:** cookies, query strings, PartFlow headers, request bodies, tokens, badges, passwords and scanned
  values. The first-run setup token is the one announced exception.
- **Size.** Each container keeps at most 5 log files of 10 MiB. As a rough guide, 1,000 production commands write about
  1.1 MB of backend log and 0.6 MB of `web` log, and every open board or Tracking screen adds a `web` record every
  15 s, so the logs cover fewer days on a busy installation (runbook §2).

#### Scripts and monitoring

| Script in `deploy/production/` | Purpose | Procedure |
| --- | --- | --- |
| `release.sh` | Installs a new release: backup, build, migration, switch and checks. | runbook §5 |
| `smoke.sh` | Checks a running release through `web` on the loopback port. It first waits, up to 120 s, while `web` does not accept connections yet or answers 502/504 for a backend that is still starting, because `up -d` returns before they answer. | runbook §5 |
| `backup.sh` | Takes, verifies and rotates a backup. | runbook §3 |
| `restore-test.sh` | Restores a backup into a throwaway stack and checks it. | runbook §4 |
| `check.sh` | Run every 15 minutes by the host scheduler, whose failure notification is the alert. It checks HTTPS readiness, the certificate, the containers, restarts, backend errors, the disks and the read-only `status` report, never changes anything, and exits non-zero when there is something to report. | runbook §2 |
| `scheduled-reconcile.sh` | Runs the full reconciliation once a day, after the daily backup. | runbook §7 |
| `reconcile_regression.py`, `monitor_report.py` | Helpers that the scripts call; never run them by hand. | — |

`check.sh` fails, by default, when the newest backup is older than 26 hours, a disk has less than 15 % free space, the
certificate expires within 21 days, a container restarted, or `backend` logged a new error; an unchanged failure is
reported again after 6 hours.

## 4. Choosing a host

| Host | Fit | Role |
| --- | --- | --- |
| Synology NAS with Container Manager | Good for a small internal installation when the NAS has reliable storage, enough memory, an uninterruptible power supply (UPS), monitoring and tested backups | Production now |
| Linux VPS | The most portable target, with predictable Docker control and independent off-site recovery | The upgrade path when the NAS is no longer enough or an external host is needed |
| Shared hosting such as Hawk Host | Conditional and not a drop-in replacement | Only when the provider proves every requirement of `deployment/SHARED_HOSTING.md`; otherwise a VPS |

Moving from the NAS to a VPS is a reinstallation of the same release plus a verified backup and restore (§8), not an
application rewrite.

## 5. Go-live requirements

There is no pilot and no list of release gates (§1.4). The first list is what PartFlow needs in order to work at all;
the second protects the factory data and is set up after go-live, when convenient.

### Needed for PartFlow to work

- **HTTPS at the reverse proxy.** The production stack marks the sign-in cookie secure, so browsers keep it only over
  HTTPS, and it publishes `web` on `127.0.0.1` only, so the reverse proxy is the single way in. The proxy must accept
  request bodies of at least 5 MiB and wait at least 300 s, and every workstation and Scan Station must trust the
  certificate (§3.1, Platform proxy requirements and Certificate requirements).
- **First-run setup** with one backend worker until the first Administrator exists (§3.1, Backend processes and
  first-run setup), then each Scan Station device enrolled in Administration → Scan Stations. Enrollment codes and
  device tokens are bearer credentials: like session cookies, they travel only over HTTPS. Revoke a lost or retired
  device there.
- **No `partflow-staging` Compose project on the production Docker daemon** (§6).
- **Releases from a git tag.** `release.sh` installs only the checked-out tag, named as §10.2 describes, with unchanged
  build inputs, and it needs an operator and an approver name. By default it takes a verified backup before every
  release.

### Recommended after go-live

Each item is one task in the platform guide; the procedures are in the runbook.

- The daily backup and an encrypted copy off the host (runbook §3). Until it runs, the only backups are the ones
  `release.sh` takes before each release.
- One timed restore drill, to know that a backup restores and how long it takes (runbook §4).
- `check.sh` every 15 minutes and `scheduled-reconcile.sh` daily, with the scheduler's failure notification (runbook
  §2 and §7).
- Firewall rules that admit only the intended networks, correct host time, and UPS coverage.
- Controlled security updates of the host, of Docker and of the base images.

### Hot list check

The check "Hot list entries are active demand" (reconcile check (i)) runs the query below. On a healthy database it
returns no rows; a returned row is a Hot list entry whose demand is no longer active, which is removed in Management →
Priority:

```sql
SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity);
```

## 6. Environment separation

PartFlow environments never share a database, secrets, a host name or a backup folder:

- **Development** runs on a workstation with `compose.yaml` and developer data only. It is also where a new release is
  tried before production (root [`README.md`](../README.md)).
- **Staging** is optional and holds only synthetic or sanitized data. It never runs on the Docker daemon of the
  production host. The Deployment Admin tool `pf` can manage a staging instance on another host
  ([`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md)).
- **Production** runs on the production host with `compose.production.yaml` and the factory's real data. It has its
  own Compose project, database volume, secrets folder, host name and backup folder.

Rules:

- Never point two environments at the same database, and never copy production data into development without the
  owner's explicit approval and a sanitized copy.
- Production starts from a new, empty database. A deliberate restore into production follows the runbook §6 (path 3,
  or the new-instance restore after a host failure), with the owner's approval recorded before `backend` starts.
- No second PartFlow stack runs on the production Docker daemon. `release.sh` refuses to run while a Compose project
  `partflow-staging` exists there.
- Every environment sets the factory's time zone: `SITE_TIMEZONE` in development (an IANA name such as
  `America/Los_Angeles`; `UTC` when unset) and `PARTFLOW_SITE_TIMEZONE` in production. The backend refuses an unknown
  name at startup. It uses this zone, never a browser's local time, to date Work Order completions (and therefore the
  Done range and the on-time or late outcome) and the default received date of new and imported Work Orders. Use the
  same value everywhere.

## 7. How a release reaches production

Every release follows the same order. `deploy/production/release.sh` performs steps 4 to 11 itself, except the checks
by hand of step 9; the procedure, its options and what to do when it stops are in the runbook §5. Never install from
a branch or with an automatic updater: every release is a tag installed on purpose.

1. Choose the release tag and its commit (§10).
2. Check that continuous integration passed for that exact commit.
3. Read what changed since the running release, especially the database migrations.
4. Take a verified backup just before the change; with a pending migration, take it inside the write freeze.
5. Build the new images without touching the running ones.
6. Start the write freeze when a migration is pending.
7. Run the migration once, in one transaction, and keep its report.
8. Start the new `backend` while `web` still serves the old screens (their writes are refused), then switch `web`,
   which reopens writes.
9. Run the smoke checks (`smoke.sh`), then check by hand with test data: sign-in and permissions, scanning at a Scan
   Station, and a change that is saved and read back.
10. Run the reconciliation before and after the change; a new finding after the switch stops `backend` again.
11. Record the release, the migration, the operator, the times and the results.
12. Keep the previous release's images and the backup until you are sure you will not roll back.

## 8. Moving to another host

A move (for example from the NAS to a VPS) reinstalls the **same** release on the new host and restores a final backup
taken inside a write freeze on the old host. The two installations must never accept writes at the same time. The
procedure is in the runbook §6 (Moving to another host).

## 9. External references

These references describe platform capabilities, not PartFlow readiness:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager) and its
  [Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7).
- [Synology Login Portal and reverse proxy help](https://kb.synology.com/en-in/DSM/help/DSM/AdminCenter/system_login_portal_advanced).
- [Cloudflare Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/) and
  [Origin Rules](https://developers.cloudflare.com/rules/origin-rules/).
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  and [remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  (`deployment/SHARED_HOSTING.md` explains why they do not prove compatibility).

## 10. Release and versioning

This section owns PartFlow's release naming, release notes and publication checklist. A release is a traceable
candidate for deployment, not proof that it is ready for production; it is installed as §7 describes.

### 10.1 Release identity

One release covers the frontend, the backend and the migrations, all from the same commit. Record its git tag and full
commit. Alembic revisions identify the database schema separately, and package metadata is not release history. Both
production images carry the release, and `GET /api/health` reports it (`release`, `commit`).

Create a release for a version chosen for testing or deployment, not for every commit. Reinstalling the same version
needs no new release. Never move, overwrite or reuse a published release tag, and never replace its published files:
changed content needs a new version.

Use versioned tags, not `Stage`, `Production`, `Latest` or phase numbers. Existing tags of another form may remain as
history but never become moving deployment targets. The environment and the purpose belong in the release notes.

### 10.2 Version convention

Use [Semantic Versioning](https://semver.org/spec/v2.0.0.html) with a lowercase `v` prefix for git tags:
`vMAJOR.MINOR.PATCH`, optionally followed by `-alpha.N`, `-beta.N` or `-rc.N`, where `N` starts at 1 and has no
leading zero. `release.sh` refuses a production release whose tag has another form.

| Situation | Example |
| --- | --- |
| First internal development snapshot chosen for testing | `v0.1.0-alpha.1` |
| Another snapshot of the same planned release | `v0.1.0-alpha.2` |
| A new development milestone with a clearly larger scope | `v0.2.0-alpha.1` |
| The intended scope is implemented; broader testing remains | `v0.2.0-beta.1` |
| Candidate for the first stable production release | `v1.0.0-rc.1` |
| First stable production release | `v1.0.0` |
| Compatible bug fix after `v1.0.0` | `v1.0.1` |
| Compatible new feature after `v1.0.0` | `v1.1.0` |
| Breaking change to the supported contract after `v1.x` | `v2.0.0` |

These are examples, not reserved tags or a required sequence. Choose the next unused version from the real release
history and the scope of the change. Before 1.0, compatibility is not guaranteed, but breaking changes are still
documented. The supported contract includes the documented API behaviour and the configuration and data-upgrade
requirements; a database migration alone does not require a new major version, but its compatibility impact is
assessed.

`alpha`, `beta` and `rc` express maturity, not the host. Mark all three as GitHub pre-releases and never as the latest
stable release. A suffix never proves that a deployment check passed.

### 10.3 Release title and description

Use the title `PartFlow <tag>` with an optional short purpose, for example:
`PartFlow v0.1.0-alpha.1 — Internal Staging`. Write release titles and descriptions in English unless asked otherwise. For the first release, summarize
the implemented scope; for later releases, describe the changes from the previous release to the exact target commit,
never uncommitted work. Use this template and replace every placeholder with verified information:

```markdown
## Summary
<Release purpose and intended use.>

## Changes
<Implemented additions, fixes, and breaking changes since the previous release;
summarize available functionality for an initial release.>

## Deployment
- Source commit: <full commit SHA>
- Previous release: <tag, or Initial release>
- Intended use: <internal staging, release validation, or production>
- GitHub pre-release: <Yes or No>
- Deployment guide: <repository guide path>

## Database and configuration
- Migrations: <required revisions and upgrade notes, or No new migrations>
- Configuration: <required changes, or No changes>
- Rollback: <application/schema compatibility and recovery requirements>

## Known limitations
<Relevant restrictions and unavailable features.>

## Validation
<Completed checks and their evidence for this exact commit;
identify pending or unverified checks explicitly.>
```

Never infer "No new migrations", compatibility or a passed check from the release name: check the revision range and
the actual evidence. A result that is not known stays `Pending` or `Not verified`. Keep host smoke-test results apart
from CI results, and never put secrets, credentials or private deployment details in public release notes.

### 10.4 Publication checklist

1. Choose the exact target commit and the previous release to compare with. Resolve local-only changes first.
2. Check local and remote tags for a name collision. Confirm that CI and the relevant quality checks passed for that
   exact commit; a run for another commit does not count.
3. Review the changes, migrations, configuration, rollback requirements and known limitations, and complete the
   release notes.
4. Create an annotated tag at the chosen commit (never at an unchecked branch tip) and push that tag.
5. In GitHub Releases, draft a release from the existing tag: enter the title and description, set the pre-release
   flag as §10.2 says, and attach any intended files.
6. Publish, then install it as §7 and the platform guide describe, and keep the previous release and its backups.

A draft can be prepared before the checks finish. Publishing a release and installing it are separate operations, and
a request to prepare release information authorizes neither.

### 10.5 Current workflow and rollback boundary

`.github/workflows/ci.yml` runs for pushes to `main` and for pull requests. It checks the code, builds the development
images and the production images with throwaway values, and runs the production static tests. It publishes no image
and deploys nothing when a tag or release is created. GitHub source archives are not prebuilt production images, and
a fixed tag does not guarantee identical later builds when base images change.

Manual releases are enough at this stage. The deployment record keeps the local image IDs of every release. When
production images are later published to a registry, record their digests beside the release tag. Do not add a
separate version service, release branch hierarchy or automatic publisher only to apply this convention.

Switching application tags never undoes a migration: a rollback confirms schema compatibility or restores the database
(runbook §6), and accounts for the writes made after the backup. Never assume that restoring an older backup keeps
newer production Movements.

References: [Git tags](https://git-scm.com/docs/git-tag) and
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-12 | uncommitted | `smoke.sh` waits for a starting `web` and backend before its checks. |
| 2026-11-10 | `e0e5056` | Rewritten as the starting point: a task table, one list of terms and safety rules, and a reference-only §3.1; the installation, release and rollback steps now live only in the platform guides and the runbook; access from the Internet is supported through Cloudflare. |
| 2026-11-09 | `398c617` | Restored rules the rewrite had dropped: the configuration key test, setup-token log access, device credentials and revocation, security updates and the version-suffix caveat. |
| 2026-11-09 | `b405c6e` | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-14 | `2c360cb` | Pointed to the Deployment Admin's planned reusable-controller design goal. |
| 2026-10-09 | `03bd90c` | Added structured logs, request ids, the status command and the monitoring scripts. |
| 2026-10-08 | `a330383` | Added verified backups, the isolated restore drill and the pre-release backup inside the write freeze. |
| 2026-10-08 | `1116b07` | Added the separate database roles and their grants. |
| 2026-10-08 | `b820e7a` | Added release identity, readiness, the write gate and `release.sh`. |
| 2026-10-08 | `d7e86a9` | Added the production Compose stack, its images and its configuration inventory. |
| 2026-10-07 | `3fb66df` | Added the read-only reconciliation command. |
| 2026-09-08 | `8d358ee` | Added the release and versioning conventions. |
| 2026-09-02 | `d2f68de` | First version of the deployment guide with its Vietnamese mirror. |
