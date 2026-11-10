# PartFlow Deployment Guide

> **Language:** English is the source of truth. [Tiếng Việt](./DEPLOYMENT.vi.md).

This guide is the starting point for installing and operating PartFlow outside a developer workstation. It is written
for the person who installs, releases and maintains PartFlow: today that is the owner, who is both operator and
approver. It explains which stack to use, what the production stack contains and how it is configured, what is needed
before real use, and how releases are named and published. Read §1 to §3 first. The platform guides in §8 apply this
guide to a particular host, and [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) holds the
day-to-day procedures (health, backups, restore, releases, rollback, reconciliation and incidents).

Commands in this guide use the shell variable `PF`, which you define once in the directory of the release checkout:

```sh
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

`PF` is only a shorthand for the Docker Compose command of the production stack. It is not the Deployment Admin tool
`pf`. Run every `$PF` command from the directory of the release checkout.

## 1. Current status

**What exists.** The repository contains a complete production stack (§3.1): production images for the backend and
for the `web` tier, the Compose file `compose.production.yaml`, a configuration and secret inventory, separate database
roles, a release script that freezes writes, backs up, migrates and switches releases, verified backups with an
isolated restore drill, structured logs, a read-only reconciliation command and host-check scripts for scheduled
monitoring. Implementation order and history are in [`IMPLEMENTATION_ROADMAP.md`](./IMPLEMENTATION_ROADMAP.md).

**What has not yet been run on a real host.** These commands and scripts have been run on Docker Desktop and in a
Linux container, but not yet on a Synology NAS or a VPS. The host settings (the DiskStation Manager (DSM) reverse
proxy, the certificate, the scheduled tasks and their failure notification, and the resource limits measured on the
host) are made during the installation on the NAS and afterwards.

**Owner decision (2026-11-09): direct go-live.** PartFlow goes into real use on the company Synology NAS as soon as the
production stack is installed. There is no pilot phase and no list of release gates. Problems are fixed in later
releases while the system is in use. Only what the software needs in order to work is required (§5); backups and
monitoring are recommended after go-live and never block use or a release.

| Use | Status | How |
| --- | --- | --- |
| Developer workstation | Supported | Use `compose.yaml` as documented in the root README. |
| Internal Synology staging or test | Supported with restrictions | Local area network (LAN) only, synthetic or non-production data, controlled users and explicit backups. See [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Production use | Supported | Install the production stack (§3.1) on the factory LAN with the requirements of §5; the NAS steps are in [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §8. Later releases use `release.sh`. |
| Internet exposure | Not supported | PartFlow runs on the factory LAN only. Opening it to the Internet needs its own owner decision (firewall, virtual private network (VPN) or a public certificate). `compose.yaml` exposes development services (§2). |

## 2. Why the development Compose stack is not for production

`compose.yaml` and the default `development` stage of each Dockerfile are development artifacts. Each Dockerfile also
holds a `production` stage, which only the production stack builds (§3.1). Never run production from `compose.yaml`,
because in that stack:

- the backend starts Uvicorn with `--reload`;
- the frontend runs the Vite development server instead of serving an immutable production build;
- source and dependency directories are bind-mounted into the containers;
- the PostgreSQL, backend and frontend ports are published on the host;
- development credential defaults exist;
- the database and the application share one PostgreSQL role (the production stack uses separate roles, §3.1);
- there is no reverse proxy, Transport Layer Security (TLS) policy, secret store, log rotation, release image tag,
  scheduled backup, restore drill or rollback command;
- several approved views are still development-only previews or wait for real backend and frontend integration.

Sign-in, server-side permission checks and the enrolled-device requirement of Scan Station routes work the same in both
stacks. Never hide the limitations above behind a NAS reverse proxy or a public DNS name.

## 3. Target portable topology

The production stack uses one topology on the Synology NAS and on a future VPS. TLS ends at the platform proxy (the DSM
reverse proxy on the NAS, or Caddy on a VPS). Inside the stack, `web` (nginx) serves the immutable frontend build and
forwards `/api` to the backend; `web` is published on the host loopback address only, so the platform proxy is the
single entry point.

```text
Browser / barcode workstation
            |
          HTTPS
            |
Platform TLS proxy (DSM reverse proxy | Caddy on a VPS)
            |   http://127.0.0.1:<port>  (loopback only)
            v
web (nginx): static build, SPA fallback, request limits, rate limits
            |
          /api
            v
FastAPI backend
            |
    private container network
            |
        PostgreSQL
```

Required boundaries:

- expose only HTTPS to clients, and HTTP only as a redirect to HTTPS;
- keep PostgreSQL private and never publish port 5432 to an untrusted network;
- keep the backend private; `web` reaches it on the private container network;
- serve the frontend and the API from one origin, so the browser keeps using the relative `/api` URLs;
- keep PostgreSQL data and backups outside the containers, which can be replaced at any time;
- run schema migration as an explicit release step, never as a side effect of an application container starting;
- identify every deployment by an immutable Git tag and commit;
- use one backup format on the NAS and on a VPS (the backup artifact of
  [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) §3).

### 3.1 Production stack

This section is the contract of the production stack. It defines the services, the configuration file and the secret
files, how images are built, how the stack is first installed, and the behaviour of health, releases, the database
roles, backups and monitoring. The step-by-step procedures are in
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md); the host settings are in the platform guides
(§8).

#### Services and networks

`compose.production.yaml` defines the Compose project `partflow-production`:

| Service | Role | Network | Notes |
| --- | --- | --- | --- |
| `db` | PostgreSQL `postgres:16.14`, Debian variant | `internal` (no external route) | Never an `-alpine` image: collation and the canonical identity check (reconcile check (j)) depend on the GNU C Library (glibc). Volume `postgres_data`; no published port; 60 s stop grace period; runs with `log_error_verbosity=terse`. |
| `backend` | image `partflow/backend:${PARTFLOW_RELEASE}`, `production` stage | `internal`, `edge` | Connects as the application role `partflow_app` and mounts only `partflow_app_password`. `SESSION_COOKIE_SECURE=true`, `ENFORCE_CLIENT_RELEASE=true` and `DATABASE_ROLES_REQUIRED=true` are fixed. `WEB_CONCURRENCY` comes from `PARTFLOW_BACKEND_WORKERS` (default 2) and `FORWARDED_ALLOW_IPS` is the edge subnet. 200 s stop grace period, above `web`'s longest upstream timeout of 180 s. `restart: unless-stopped`, never `on-failure`, because with several workers a configuration refusal exits with code 0. |
| `web` | image `partflow/web:${PARTFLOW_RELEASE}`, `production` stage | `edge` | The only published port, `127.0.0.1:${PARTFLOW_HTTP_PORT}:80`; there is deliberately no variable for the bind address. No `depends_on`, so it keeps serving the application shell while `backend` is stopped. 200 s stop grace period, so an in-flight import can finish. |
| `migrate` | one-shot `python -m app.cli migrate` from the backend image | `internal` | Profile `ops`, never started by `up`. One connection, one transaction. Without a backup option it is a usage error. Mounts the backup directory read-only at `/backups` to verify the pre-release backup. |
| `db-roles` | one-shot `python -m app.cli provision-roles` (default) or `apply-grants` (argument) from the backend image | `internal` | Profile `ops`. Connects as the owner role and mounts the three secret files. |
| `backup-tools` | one-shot `python -m app.cli` from the backend image, run with `backup-manifest`, `backup-verify` or `backup-rotate` (used by `deploy/production/backup.sh`) | none (`network_mode: none`) | Profile `ops`. No database and no secret; mounts only the backup directory at `/backups`. |
| `status` | one-shot `python -m app.cli status` from the backend image | `internal` | Profile `ops`. A read-only report as `partflow_app`, with the backup directory mounted read-only (used by `deploy/production/check.sh`). |

Every service has a restart policy, memory and processor limits taken from the configuration file, and `json-file` log
rotation (10 MiB per file, 5 files). Every long-running service has a health check; the `backend` check calls the
liveness route, so a schema mismatch never makes a container unhealthy. The resource limits are starting values;
measure them on the production host and adjust. Run the `ops` services that read or write the backup directory with
`--user "$(id -u):$(id -g)"`, because that directory has mode 0700 and belongs to the operator's account.

#### Configuration file

`.env.production` is copied from `.env.production.example`, is ignored by git, has mode 600 and holds only non-secret
values. A static test checks that the set of `${NAME}` references in `compose.production.yaml` and
`compose.production.build.yaml` equals the set of keys of the example file.

| Key | Meaning | Required or default |
| --- | --- | --- |
| `PARTFLOW_RELEASE` | Tag of the **running** images (§10). | required |
| `PARTFLOW_COMMIT` | Full commit of the release being built. Leave it empty in the file; `release.sh` passes it in the shell at build time. | required to build |
| `PARTFLOW_ACCEPT_SCHEMA_REVISION` | Rollback path 2 only: the one database revision the running release may serve (`deployment/OPERATIONS_RUNBOOK.md` §6). `release.sh` clears it. | empty |
| `PARTFLOW_SECRETS_DIR` | Absolute path of the secrets directory, outside the checkout, production only (directory mode 0700, each file mode 0444). | required |
| `PARTFLOW_BACKUP_DIR` | Absolute path of the production backup directory, where `backup.sh` writes one sub-directory per backup. It lies outside the checkout, the secrets directory and any archive directory, and is never staging's or a drill's. It must already exist with mode 0700, owned by the account that runs `backup.sh` and `release.sh`. **Every** `$PF` command needs it; write it unquoted. The platform backup tool encrypts it and copies it off the host. | required |
| `PARTFLOW_SITE_TIMEZONE` | Factory calendar time zone, equal to the staging value (§6). | required |
| `PARTFLOW_HTTP_PORT` | Loopback port the platform proxy connects to. | required (example `18080`) |
| `POSTGRES_USER`, `POSTGRES_DB` | Owner role and database. | required (example `partflow_owner`, `partflow`) |
| `PARTFLOW_BACKEND_WORKERS` | Number of Uvicorn workers. | `2` |
| `PARTFLOW_EDGE_SUBNET` | Subnet of the `edge` network, also the addresses Uvicorn trusts for forwarded headers. Choose one that is unused on the host and the LAN. | `172.30.250.0/24` |
| `PARTFLOW_TRUSTED_PROXY` | One IPv4 address `web` trusts for forwarded headers; empty means auto-detect (Platform proxy, below). | empty |
| `PARTFLOW_{DB,BACKEND,WEB,OPS}_{MEMORY,CPUS}` | Resource limits. | `1g`/`1.0`, `1g`/`2.0`, `128m`/`0.5`, `512m`/`1.0` |

Fixed in Compose and not configurable: `SESSION_COOKIE_SECURE=true`, `ENFORCE_CLIENT_RELEASE=true`, `DATABASE_HOST=db`,
`DATABASE_PORT=5432`, the secret mount paths and the loopback bind.

The backend takes its database connection from exactly one of two forms: `DATABASE_URL` (development, tests,
continuous integration (CI) and staging), or `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` and
`DATABASE_PASSWORD_FILE` with an optional `DATABASE_PORT` (default 5432), which the production stack uses. Both forms
present, or neither complete, is refused at startup. The application builds the connection URL itself, so special
characters in a password need no manual encoding. No validation error echoes an input value.

#### Secret files

No secret is an environment value and none has a committed default. Secrets are files in `PARTFLOW_SECRETS_DIR`,
mounted under `/run/secrets`:

| File | Used by | Rules |
| --- | --- | --- |
| `postgres_password` | owner role: `db`, `migrate`, `db-roles` | One line. `db` reads it **only when a new data volume is initialized**; `migrate` and `db-roles` read it at every start. `backend` never mounts it. |
| `partflow_app_password` | `backend`, `status`, `db-roles` | One line, 16 to 128 printable ASCII characters without spaces. |
| `partflow_maintenance_password` | `db-roles` | Same rules, and different from `partflow_app_password`. |

Create the secret files as follows, before the first `$PF` command that starts `backend` or an `ops` service. Compose
replaces a missing file with an empty directory, and `release.sh` refuses, changing nothing, while any of the three
files is missing, empty or not a regular file. If Compose has already created a directory at that path, remove it
first.

```sh
install -d -m 0700 "$PARTFLOW_SECRETS_DIR"
for name in postgres_password partflow_app_password partflow_maintenance_password; do
    openssl rand -base64 32 > "$PARTFLOW_SECRETS_DIR/$name"
    chmod 0444 "$PARTFLOW_SECRETS_DIR/$name"
done
```

Set `PARTFLOW_SECRETS_DIR` in the shell to the same absolute path as in `.env.production` before running these
commands. Each file must hold exactly one line (trailing line breaks are ignored), because PostgreSQL's `initdb` takes a
new role's password from the first line only. A multi-line, empty, unreadable or non-UTF-8 file is refused with a
message that names the file path and never its content.

To change a password later:

- **Owner password:** run `ALTER ROLE <POSTGRES_USER> PASSWORD …` inside `db` first, then replace the file. No service
  restart is needed.
- **Role password:** replace its file, run `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, then
  `$PF up -d --force-recreate --no-deps backend`. The backend reads its password file once per process, so it must be
  recreated. Do this in a short write freeze (`deployment/OPERATIONS_RUNBOOK.md` §9).

#### Building images

Images are built locally from the checked-out release and never pulled (`pull_policy: never`). Only the build-only
companion file `compose.production.build.yaml` contains a build section, and it is used only with `build`:

```sh
PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
```

Always build both images together. Because `compose.production.yaml` has no build section, `up` or `run` with a tag
whose images are missing fails with `No such image` instead of building the current checkout under that tag. An
existing tag is never rebuilt.

- **`backend` image (`production` stage):** Python 3.12 slim, the locked non-development dependencies, no `tests/`,
  no `.env` and no reload server; it runs as user `10001:10001` with the start command
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --log-config app/core/logging.production.json`.
  It never runs migrations on start.
- **`web` image (`production` stage):** the pinned official `nginx:1.30.5-alpine` with the immutable frontend build
  from `npm run build`, which includes the production-boundary check (no development preview data in the build).

#### Process model and first run

`WEB_CONCURRENCY` sets the number of Uvicorn workers and `FORWARDED_ALLOW_IPS` the proxy addresses Uvicorn trusts for
forwarded headers. Compose sets both inside the container, so a `WEB_CONCURRENCY` value in the operator's shell has no
effect.

- **First-run setup runs with one worker.** Each worker has its own first-run setup token and its own password-hashing
  limits. With two workers, a setup request can reach the worker whose token was not copied and is refused with
  `403 setup_token_invalid` (nothing is written). Start the backend with `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`,
  read the token with `$PF logs backend | grep "Setup token"`, complete setup in the browser, then restore the
  configured worker count with `$PF up -d backend`. The setup token is the only secret ever written to the backend
  log: complete first-run setup before opening access to users, and restrict access to the log until then.
- **A configuration refusal exits with code 0** when there is more than one worker. The log line and the restart
  count, not the exit code, show the failure.
- **Stopping a multi-worker backend does not refuse new connections at once.** The Uvicorn supervisor keeps the
  listening socket open until its last worker has exited (up to the 200 s stop grace period while an import
  finishes). A request sent meanwhile is accepted but never answered: it ends as `web`'s 504 after the 60 s read
  timeout, or as a 502 when the supervisor exits. The backend never ran it, but the client must treat it as an unknown
  outcome. With one worker such a request is refused at once (502). Therefore start a write freeze when no import is
  running.

**First installation.** Run these steps from the release checkout, with the release tag already set as
`PARTFLOW_RELEASE` in `.env.production`:

1. Create the backup directory with `install -d -m 0700 <path>` and set `PARTFLOW_BACKUP_DIR` in `.env.production`;
   every Compose command of the stack needs this key.
2. Create the three secret files (Secret files, above).
3. Build both images: `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`.
4. Start the database: `$PF up -d db`.
5. Create the database roles: `$PF --profile ops run --rm -T db-roles`.
6. Apply the schema and the grants:
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`.
7. Run first-run setup with one worker (above), then start the application: `$PF up -d backend web`.
8. Run `$PF run --rm --no-deps -T backend python -m app.cli reconcile` and expect the check "Append-only guards and
   database-role privileges intact" (check (h)) to report `pass`.

#### Health and release identity

A release is the tag `PARTFLOW_RELEASE` plus the full 40-character commit `PARTFLOW_COMMIT`. Both images of a release
are built from one commit and carry the same identity.

- `compose.production.build.yaml` passes `PARTFLOW_RELEASE` and `PARTFLOW_COMMIT` as build arguments to both images.
  The build fails without a valid tag (letters, digits, `.`, `_` and `-`, at most 64 characters, not `development`) or
  without a lowercase 40-hexadecimal commit.
- Both images store the identity as the labels `org.opencontainers.image.version` and
  `org.opencontainers.image.revision`. The `web` image also bakes the tag into the frontend bundle
  (`VITE_PARTFLOW_RELEASE`) and into the `partflow-release` meta tag of the served page.
- The backend reads `RELEASE_TAG` and `RELEASE_COMMIT`, which the `production` stage sets (outside a release image
  they default to `development` and none). Compose never sets `RELEASE_TAG`.
- Every page sends the release of its bundle in the request header `X-PartFlow-Release`; the comparison with the
  backend's release is exact string equality.

The backend has two health routes:

- **Liveness, `GET /api/health/live`**, answers `{"status":"live","service","release","commit"}` without touching the
  database. Every container health check uses it.
- **Readiness, `GET /api/health`**, reads the database revision. Its keys are `status`, `service`, `database`,
  `release`, `commit`, `schema`, `expected_revision`, `database_revision` and `accepted_revision`. It answers `200`
  with `"status":"ok"` only when `schema` is `current` or `accepted`, `503 not_ready` on a `mismatch`, and
  `503 unavailable` with `"schema":"unknown"` when the database is unreachable.

| `schema` value | Meaning |
| --- | --- |
| `current` | The database revision equals the image's single Alembic head. |
| `accepted` | The database is at the one revision named by `PARTFLOW_ACCEPT_SCHEMA_REVISION`, which this image does not know (rollback path 2, `deployment/OPERATIONS_RUNBOOK.md` §6). Naming a revision the image knows is ignored with a warning, and `revision` reports `override_ignored`. |
| `mismatch` | Any other database revision. |
| `unknown` | The database cannot be read. |

#### Write gate

The backend refuses every `POST`, `PUT`, `PATCH` and `DELETE`, on any path, before routing, the cross-site request
forgery (CSRF) check or reading the body, in two cases. Both refusals write nothing and carry `Cache-Control: no-store`.

| Answer | When | Client meaning |
| --- | --- | --- |
| `409` with `release_mismatch: true` | Enforcement is on and the request's `X-PartFlow-Release` is missing or differs from `RELEASE_TAG`. | Definite refusal: that request recorded nothing (an earlier unanswered attempt stays unknown). |
| `503` with `not_ready: true` | The schema is a `mismatch` or readiness cannot be confirmed; the gate fails closed. | Unknown outcome. |

Reads and health routes are never gated. Enforcement (`ENFORCE_CLIENT_RELEASE`) is fixed on in
`compose.production.yaml` and off in development and tests; the backend refuses to start with enforcement on and the
`development` tag. The one write on a safe method, the `last_seen_at` bookkeeping of a Scan Station device, holds no
production data and is accepted under a mismatch. The screen state for these answers is `GUI_DESIGN.md` §3 rule 13.

#### Migrations and the `revision` command

`python -m app.cli migrate` (the `migrate` service) applies the pending migrations in one transaction on one
connection, applies the grants in the same transaction (Database roles and grants, below) and prints a JSON report.

- It needs exactly one of `--pre-release-backup NAME` or `--no-backup-reason TEXT`. `NAME` is a backup directory taken
  by `backup.sh`; it is verified before the migration and recorded, and with a pending migration `migrate` also needs
  `--backup-not-before UTC`, the time the write freeze started. The reason text is recorded (for example
  `first install: empty database`). The freshness and ownership rules are in `deployment/OPERATIONS_RUNBOOK.md` §5.
- It accepts `--lock-timeout SECONDS` (1 to 600, default 30) and `--backup-dir` (the mount, default `/backups`).
- It refuses, changing nothing: a revision file with non-transactional schema changes (`autocommit_block`,
  `CONCURRENTLY`), a database revision this release does not know, a concurrent `migrate`, and a connected backend
  session (a best-effort check, not proof that `backend` is stopped). A database already at the head answers
  `already_current`.
- A role or grant refusal (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`,
  `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) is the result `refused` with exit code 1,
  and the upgrade of the same run is rolled back.

The backend never migrates. `python -m app.cli revision` is read-only: it prints the release identity, the expected
and database revisions, the pending revisions and the readiness the backend would report, and exits with 0 only when
the schema is `current`.

#### Database roles and grants

The production database has three roles:

| Role | Used by | Notes |
| --- | --- | --- |
| Owner (`POSTGRES_USER`, default `partflow_owner`) | `db`, `migrate`, `db-roles`, backups | The PostgreSQL bootstrap superuser; it owns every object. Its name must differ from the two roles below. |
| `partflow_app` | `backend` (the API, `reconcile`, `revision` and the recovery commands) and `status` | Holds only the privileges in the table below. |
| `partflow_maintenance` | no service yet | Holds SELECT on a named list of tables only; reserved for the planned archival maintenance. |

`provision-roles` creates both non-owner roles as `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
NOBYPASSRLS`, with no membership, no per-role setting and no owned object. Their names are fixed in code and in
`compose.production.yaml`.

| Class | `partflow_app` privileges | Tables |
| --- | --- | --- |
| append-only | SELECT, INSERT | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| guarded update | SELECT, INSERT, UPDATE | `worker_sessions` (its row trigger limits the columns) |
| no update | SELECT, INSERT, DELETE | `assigned_route_steps` |
| read only | SELECT | `alembic_version` |
| ordinary | SELECT, INSERT, UPDATE, DELETE | every other table |

No role holds TRUNCATE, a sequence privilege (the tables use identity columns), a grant option, or `CREATE` on schema
`public` or on the database, and the PUBLIC and default privileges are removed. The full classification is
`SLICE1_DATA_MODEL.md` §17. The triggers that raise an error on a forbidden write are the first layer of protection;
the revoked privileges are the second. A `permission denied` error (SQLSTATE 42501) in the backend log is therefore an
incident (`deployment/OPERATIONS_RUNBOOK.md` §2) and is never fixed by granting more. A superuser that disables a
trigger is outside what PartFlow can detect; this is an accepted limit.

- `provision-roles` reads `partflow_app_password` and `partflow_maintenance_password` before it connects, creates or
  repairs the two roles and sets their SCRAM-SHA-256 passwords in one transaction. It is safe to repeat.
- `apply-grants` derives every grant from the table classification in code and applies it in one transaction. It
  refuses unless the database is at the release's Alembic head.
- Every `migrate` runs the grants in its own transaction after the upgrade, so a release never leaves a new table
  without its grants. A table without a class, a role with a forbidden attribute or membership, a role that owns
  objects, or a privilege granted by a role PartFlow does not manage rolls the whole run back (`migrate` result
  `refused`, exit code 1, nothing changed).
- With `DATABASE_ROLES_REQUIRED=true` (fixed on `backend` and `migrate` in `compose.production.yaml`), missing roles
  are refused with `roles_not_provisioned`. Without it (development, test and staging) the grants report is
  `not_provisioned` and nothing is granted.
- Grants are never restored from a dump. Every `migrate` re-derives them from the code, and after a restore you run
  `apply-grants`.
- The check "Append-only guards and database-role privileges intact" (reconcile check (h)) verifies the result.

#### Releases

`deploy/production/release.sh` runs the release sequence of §7. Run it from the repository root of the release
checkout with the release tag checked out; `deploy/production/release.sh --help` prints the usage.

```sh
deploy/production/release.sh --release <new> --operator <name> --approver <name>
```

| Option | Meaning |
| --- | --- |
| `--release TAG`, `--operator NAME`, `--approver NAME` | Required. One person may give the same name as operator and approver. |
| (no backup option) | `release.sh` takes the verified pre-release backup itself with `backup.sh`, inside the write freeze when a migration is pending. |
| `--pre-release-backup NAME` | Use an existing `backup.sh` backup instead; refused when a migration is pending. |
| `--no-backup-reason TEXT` | No pre-release backup (first installation and rehearsals only); the reason is recorded. |
| `--accept-pre-release-findings` | The reconciliation before the release has findings: continue, and block only on findings absent from it. |
| `--skip-pre-reconcile REASON` | No image has the database revision as its head (the state of rollback path 2): skip the reconciliation before the release; every finding after the release then blocks. |
| `--env-file`, `--records-dir` | The configuration file (default `.env.production`) and the records directory (default `$HOME/partflow-deployments`, mode 0700). |
| `--environment`, `--url`, `--rollback-deadline`, `--observation-owner`, `--known-limitations` | Optional record fields. Text values are 1 to 500 characters, without control characters, `"` or `\`. |
| `--rehearsal --project NAME` | Rehearse on a throwaway Compose project, never `partflow-production`. |

Before it changes anything, `release.sh` refuses when the tag is not a release tag of §10.2, when the tag is not the
checked-out commit or the build inputs differ from it, when a `partflow-staging` project exists on the Docker daemon
(§6), when a secret file is missing, when the images of the running release are not on the host (rollback would be
impossible), or when another release holds the lock.

| Exit code | Meaning |
| --- | --- |
| 0 | Completed. |
| 1 | Stopped with nothing changed, or writes reopened on the current release. |
| 2 | Could not run. |
| 3 | `backend` left stopped; follow `deployment/OPERATIONS_RUNBOOK.md` §6. |
| 4 | The new release may be running and writable after a failed check. |
| 130 or 143 | Interrupted by Ctrl-C or TERM; the record names the step, and an interrupted `migrate` has an unknown outcome. |

Each step's output and `record.json` (the deployment record of `deployment/OPERATIONS_RUNBOOK.md` §1) are written to
`<records-dir>/<UTC>-<tag>/`. `deploy/production/smoke.sh --release TAG [--env-file …] [--project NAME]
[--allow-accepted-schema]` runs the loopback checks: the served page and its release, the single-page-app fallback,
the release identity and schema in health and liveness, the JSON 404 for an unknown API path, the write gate's 409
without the release header and its pass-through with it, and the running image identities.

`.env.production` always names the release that is **running**. The candidate tag lives only in the shell, or in the
script's per-command environment, until the switch, so every recovery command, reconciliation or `up` before the
switch uses the current image against the current schema. The manual equivalent of `release.sh`, in the same order, is
in `deployment/OPERATIONS_RUNBOOK.md` §5. Nothing schedules `release.sh`, no updater runs on the host, and neither
script removes, prunes or re-tags an image.

#### Rollback

The decision tree, paths 1 and 2 and the schema override are in `deployment/OPERATIONS_RUNBOOK.md` §6.

- **Application code without a schema change:** put the previous tag back in `.env.production` and run
  `$PF up -d backend web`. This needs the previous release's images on the host, so keep them through the rollback
  window and never run `docker image prune -a`; when they are gone, `up` fails with `No such image` and starts nothing.
- **Schema rollback:** restore the pre-release backup into a new database (path 3 in the same section). It needs the
  owner's recorded approval. There is never a down-migration.
- **Never run `down -v`, or remove a volume, on the `partflow-production` project:** it deletes
  `partflow-production_postgres_data`. `$PF down` without `-v` is the only stop-everything form.

#### Request limits and timeouts (`web`)

| Route | Body limit | Upstream timeout |
| --- | --- | --- |
| Default for every `/api` route | 1 MiB | 60 s |
| `PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image` | 4 MiB | 60 s |
| `POST /api/work-orders/import/preview` | 4 MiB | 60 s |
| `POST /api/work-orders/import` | 4 MiB | 180 s (read and send) |

The 4 MiB limit is above the application's own limits (2 MiB images, 1 MiB import files), so the application's JSON
413 answers an oversized but plausible file; `web` answers first only for a clearly larger body. `web` never retries a
request upstream: one client request is at most one backend execution.

**Rate limits (per forwarded client address).** `POST /api/session` allows 10 per minute with a burst of 5 (six
attempts at once, then one more every 6 s). `PUT /api/session/password`, `POST /api/setup/administrator` and
`PUT /api/users/{user_id}/password` allow 5 per minute with a burst of 4 (five at once, then one every 12 s). Reads such
as `GET /api/session` and sign-out are never limited. A 429 from `web` never reaches the application, so it never
counts as a failed sign-in and never locks an account; the per-account lockout and the per-address limit are
independent layers. Device-activation codes are not rate limited (50 bits of entropy, 15-minute lifetime).

**Answers generated by `web`.** `web` answers only for conditions it detects itself; every backend answer, including
the application's own 401, 403, 409, 413, 422 and 503, passes through unchanged.

| Status | When | Body `detail` | Client meaning |
| --- | --- | --- | --- |
| 413 | body over the location's limit | `This request is too large for PartFlow. Nothing was changed.` (`request_too_large: true`) | definite refusal |
| 429 | rate limit exceeded (sends `Retry-After: 60`) | `Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.` (`rate_limited: true`) | definite refusal |
| 502 | backend unreachable, or it closed the connection before answering | `The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | unknown outcome for a write |
| 504 | no backend answer within the timeout | `The PartFlow server did not answer in time. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | unknown outcome for a write |

A 502 or 504 keeps its 5xx status, so a station write that may have committed is shown as unknown and retried with the
same `device_event_id` (`deployment/OPERATIONS_RUNBOOK.md` §2). The frontend shows a built-in message for a 413 or 429
that carries no JSON `detail`, for example one from a platform proxy. `web` adds no cross-origin resource sharing (CORS)
headers and rewrites nothing else.

**Caching and headers.** `index.html` and every single-page-app fallback are `no-cache`; `/assets/*` are immutable for
one year, and a missing asset is a plain 404, never the application page. Every response carries
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and a baseline `Content-Security-Policy` (same-origin
scripts, styles, connections and frames; `blob:` and `data:` images for the local image previews; no inline script).
Whoever adds a feature that needs another source amends the policy in the same change. HTTP Strict Transport Security
(HSTS) is the platform proxy's responsibility; `web` does not set it.

#### Logging

- **Request id.** Every response returns an HTTP request id in `X-Request-ID`. `web` accepts a client value of 1 to 64
  characters of `A-Za-z0-9._-` or generates one, passes it to the backend and overrides the backend's copy of the
  header; the backend uses the same rule and otherwise creates a new 32-hexadecimal id.
- **`web` log.** One JSON edge record per request: client address, method, path without query string, status, bytes,
  duration, upstream status, user agent and `request_id`. Health probes are excluded.
- **Backend log.** The backend starts with `--log-config app/core/logging.production.json`: every record, Uvicorn's
  included, is one JSON line on standard error (`ts`, `level`, `logger`, `message`, `request_id`, then the record's
  fields). Uvicorn's own access log stays off. A traceback never contains a database exception message, so no `DETAIL`
  line, statement or parameter value reaches the log.
- **Application record (`app.access`).** Written at INFO for a write, for a designed refusal (`not_ready`,
  `release_mismatch`, `password_check_busy`, validation, authorization) and for a read of one second or more
  (`"slow":true`); at ERROR for a failure; at DEBUG for a health poll or a routine fast read. It names the Part Number,
  QuantityFlow, Work Order Demands, Area, Operation, Machine, Worker (only after identity resolution), Scan Station and
  `device_event_id` of a production command and the reason of a refusal.
- **Never logged:** cookies, query strings, any PartFlow header, request bodies, tokens, badges, passwords and scanned
  values. The first-run setup token is the one announced exception. `db` runs with `log_error_verbosity=terse`.
- **Size.** Containers rotate with the `json-file` driver (`max-size` 10m, `max-file` 5). As a rough guide, 1,000
  production commands produce about 1.1 MB of backend log and 0.6 MB of `web` log, and every open board or Tracking
  screen adds `web` records every 15 s, so the retention budget is per source (`deployment/OPERATIONS_RUNBOOK.md` §2).

#### Monitoring

- **`status` service.** `$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status` prints a read-only
  report as `partflow_app`, with the backup directory mounted read-only.
- **`deploy/production/check.sh`.** Run every 15 minutes by the host scheduler, whose failure notification is the
  alert channel. It evaluates the HTTPS readiness, the certificate, the containers, restarts, backend errors, the disks
  and the `status` report, prints one `PASS`, `FAIL` or `SKIP` line per check, never changes a service, and exits
  non-zero when there is a failure to notify. An unchanged failing set is notified again after 6 hours by default.
- **`deploy/production/scheduled-reconcile.sh`.** Runs the full read-only `reconcile` once a day, after the daily
  backup, and exits 0 when clean, 1 on a mismatch and 2 when it could not run.

| Check | Default threshold |
| --- | --- |
| Newest backup age | older than 26 hours fails |
| Free disk space | below 15 % fails |
| Certificate expiry | within 21 days fails |
| Container restarts | any increase fails |
| Backend errors | any new error record fails |

The commands, schedules and thresholds are in `deployment/OPERATIONS_RUNBOOK.md` §2, §7 and §9 and in the platform
guides ([`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §8, [`deployment/VPS.md`](./deployment/VPS.md) §8).

#### Platform proxy requirements

The DSM reverse proxy, or Caddy on a VPS, must:

- terminate HTTPS with a certificate that the company workstations trust, and send HTTP only as a redirect to HTTPS;
- forward to `http://127.0.0.1:<port>`, using the literal `127.0.0.1` and never `localhost`, because `localhost` can
  resolve to the IPv6 address `::1` first while `web` listens on IPv4 loopback only;
- send `Host`, `X-Forwarded-For` (with the client address appended last) and `X-Forwarded-Proto: https`;
- accept request bodies of at least 5 MiB and use send and read timeouts of at least 300 s, so that `web`'s or the
  application's JSON answer arrives first;
- never log cookies, `X-PartFlow-Station-Device` or `X-PartFlow-CSRF`;
- admit only the approved LAN or VPN sources, together with the host firewall.

**Real client address.** `web` trusts forwarded headers from exactly one hop. By default that hop is detected when the
container starts, as its default gateway on the edge network (the address that host-loopback connections arrive from).
Set `PARTFLOW_TRUSTED_PROXY` (one IPv4 address) only when the platform proxy arrives from another address. If neither
yields an address, the container refuses to start. From that hop, `web` takes the **last** `X-Forwarded-For` address as
the client and sends the backend only that address; `X-Forwarded-Proto` is honoured only from the same hop. If the
proxy cannot supply the client address, every client shares one rate-limit bucket; record this and let the owner
decide. `web` publishes no certificate and reads no TLS configuration.

The DSM settings are in [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §5 and the Caddy example in
[`deployment/VPS.md`](./deployment/VPS.md) §4.

#### Certificate procedure

The certificate names the PartFlow hostname as a subject alternative name (SAN). It is issued by one of:

- a public certificate authority (CA) that uses the Automatic Certificate Management Environment (ACME) protocol, for a
  public DNS name;
- the company's internal CA;
- a private CA created on the host for this purpose, for an internal-only name (the DSM steps are in
  `deployment/SYNOLOGY_NAS.md` §5.2).

Never use a bare self-signed certificate, and never accept a certificate workstation by workstation past a browser
warning. The root certificate of an internal or private CA is installed as trusted on every workstation and barcode
terminal, through the company's device management or by hand. The deployment administrator owns renewal; `check.sh`
monitors expiry (the `certificate` check, alert below 21 days, `deployment/OPERATIONS_RUNBOOK.md` §9). To check the
expiry from any client:

```sh
openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -subject -enddate
```

The platform steps are in `deployment/SYNOLOGY_NAS.md` §5 and `deployment/VPS.md` §4.

#### Environment separation

Production uses its own Compose project, database volume, secrets directory, hostname and backup location
(`PARTFLOW_BACKUP_DIR`); none of them is shared with or pointed at a staging or development stack.

- Production starts from a new, empty database volume, then migrations, then first-run setup. Staging or development
  data is never attached, reused or copied into it.
- A deliberate restore into production follows `deployment/OPERATIONS_RUNBOOK.md` §6 path 3 or the host-failure
  procedure of §8 (new-instance restore), and the owner's approval is recorded before `backend` starts.
- Staging managed by the Deployment Admin tool `pf` is not installed on the production Docker daemon, and `release.sh`
  refuses while a `partflow-staging` project exists there. The manual `compose.yaml` staging of
  `deployment/SYNOLOGY_NAS.md` §4 is stopped, with its containers removed and its volumes **kept**, before production
  starts.
- `PARTFLOW_SITE_TIMEZONE` equals the staging value (§6).
- Never run `down -v`, or remove a volume, on the production project: it deletes the database.

#### Operator commands

| Purpose | Command |
| --- | --- |
| Preflight (§6, environment separation) | `docker ps -a --format '{{.Label "com.docker.compose.project"}}' \| sort -u` lists no `partflow-staging` |
| Validate the configuration | `$PF config --quiet` |
| Build a release (tag and commit in the shell; the only use of the build file) | `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build` |
| Start the database | `$PF up -d db` |
| Apply migrations and grants (once per release, with `backend` stopped) | `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate (--pre-release-backup NAME --backup-not-before UTC \| --no-backup-reason TEXT)` (first install: the tag is already in `.env.production`) |
| Take a backup (daily, manual) | `deploy/production/backup.sh --kind daily --operator … --keep-daily N --keep-weekly N` · `--kind manual --operator … --reason …` (`OPERATIONS_RUNBOOK.md` §3) |
| Verify a backup, or preview the retention | `$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify NAME` · `… backup-tools backup-rotate --keep-daily N --keep-weekly N --dry-run` |
| Restore drill in an isolated project | `deploy/production/restore-test.sh --backup NAME --operator …` (`OPERATIONS_RUNBOOK.md` §4) |
| Create or repair the database roles; set or rotate their passwords | `$PF --profile ops run --rm -T db-roles` (= `provision-roles`) |
| Apply the grants outside a migrate (after a restore, or to repair drift) | `$PF --profile ops run --rm -T db-roles apply-grants` |
| Guard-integrity check (reconcile check (h), as `partflow_app`) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile --check h` |
| Privilege probe (evidence; one invocation per table and statement, zero rows, rolled back) | `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -c BEGIN -c "SET LOCAL ROLE partflow_app" -c "UPDATE part_movements SET id = id WHERE false" -c ROLLBACK'` (refused = stderr has `permission denied for table <t>`; allowed = stdout has `UPDATE 0` and no `ERROR`; the variables expand inside `db`) |
| Release (the whole sequence of §7) | `deploy/production/release.sh --release <new> --operator … --approver … [--pre-release-backup NAME \| --no-backup-reason TEXT]` |
| Smoke checks of a running release | `deploy/production/smoke.sh --release <tag>` |
| Expected and database revision, readiness | `$PF run --rm --no-deps -T backend python -m app.cli revision` |
| Start or recreate the application | `$PF up -d backend web` |
| Status and logs | `$PF ps` · `$PF logs --since=15m backend web db` |
| Health (readiness: release, schema, revisions) through `web` on the host | `curl --fail --silent --show-error http://127.0.0.1:${PARTFLOW_HTTP_PORT}/api/health` · liveness: `…/api/health/live` |
| Reconciliation (also while `backend` is stopped) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile` |
| Identity rehearsal of a candidate image (check (j)) | `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j` |
| Recovery commands | `$PF run --rm --no-deps backend python -m app.cli reset-password …` · `… restore-correction-permission-management …` |
| Write freeze | `$PF stop backend` (may wait up to 200 s while an import finishes, and requests sent meanwhile hang up to 60 s, see Process model and first run: start it when no import is running) · reopen with `$PF up -d backend` on the same release; at a release switch the reopen is the `web` switch of `release.sh` (§7) |
| First-run setup | `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, read the token with `$PF logs backend \| grep "Setup token"`, complete setup, then `$PF up -d backend` |

## 4. Platform decision

| Platform | Fit | Recommended role |
| --- | --- | --- |
| Synology NAS with Container Manager | Good for a small internal deployment when the model supports the required containers and the NAS has reliable storage, memory, monitoring, uninterruptible power supply (UPS) coverage and tested backups | Production now (owner decision 2026-11-09, §1) |
| Linux VPS | Best long-term portable target | Preferred upgrade path for production, remote access, predictable Docker control and independent off-site recovery |
| Shared hosting such as Hawk Host | Conditional and not drop-in | Use only if the provider proves native ASGI/FastAPI process support, PostgreSQL, the required routing, migrations, jobs and recovery controls; otherwise choose a VPS |

Moving from Synology to a VPS is a release redeployment plus a verified PostgreSQL backup and restore, not an
application rewrite.

## 5. Go-live requirements

There is no pilot and no list of release gates (owner decision 2026-11-09, §1); nothing in this section blocks real use
or a release. The first list is what the software needs in order to work at all. The second list protects the factory
data and is set up after go-live, when convenient.

### Needed for the software to work

- **HTTPS at the platform proxy.** `compose.production.yaml` fixes `SESSION_COOKIE_SECURE=true`, so browsers keep the
  sign-in cookie only over HTTPS, and it publishes `web` on `127.0.0.1` only, so the platform reverse proxy is the
  single entry point (§3.1, Platform proxy requirements; the DSM settings are in `deployment/SYNOLOGY_NAS.md` §5.2).
  The proxy must accept request bodies of at least 5 MiB and use timeouts of at least 300 s, otherwise image uploads
  and Work Order imports fail; the largest allowed import, 2,000 Work Orders, takes about half a minute. Every
  workstation and Scan Station must trust the certificate (§3.1, Certificate procedure).
- **First-run setup** with one backend worker until the first Administrator exists (§7 step 8; the setup token is in
  the backend log), then each Scan Station device enrolled in Administration → Scan Stations. Enrollment codes and
  device tokens are bearer credentials: like session cookies they travel only over HTTPS. Revoke a lost or retired
  device there.
- **No `partflow-staging` Compose project on the production Docker daemon.** `release.sh` refuses to run while one
  exists (§3.1, Environment separation). Try changes on a workstation instead.
- **Releases from a git tag.** `release.sh` builds only the checked-out tag (named as §10.2 describes) with unchanged
  build inputs and needs `--operator` and `--approver`; one person may give the same name to both. By default it takes
  a verified backup before every release.

### Recommended after go-live

Each item is one platform task or one command; the details are in `deployment/OPERATIONS_RUNBOOK.md`.

- The daily `backup.sh` task and an encrypted copy off the host (OPERATIONS_RUNBOOK §3). Until it runs, the only
  backups are the ones `release.sh` takes before each release.
- One timed `restore-test.sh` run, to know that a backup restores and how long it takes (OPERATIONS_RUNBOOK §4).
- `check.sh` every 15 minutes and `scheduled-reconcile.sh` daily, with the platform's failure notification
  (OPERATIONS_RUNBOOK §9 and §7).
- Firewall rules that admit only the factory networks, correct host time, and UPS coverage.
- Controlled security updates of DSM, Container Manager and the base images.

The check "Hot list entries are active demand" (reconcile check (i), `deployment/OPERATIONS_RUNBOOK.md` §7) runs the
Hot-list query below. It returns no rows on a healthy database; a returned row is an inactive Hot list entry, which is
removed in Management → Priority:
`SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity);`

## 6. Environment separation

Use separate databases, secrets, URLs and backup locations for:

- `development`: developer data only;
- `staging`: synthetic or sanitized data, release rehearsal;
- `production`: authorized factory data.

Never restore production data into development without explicit authorization and sanitization. Never point staging
and production at the same database. The production rules are in §3.1, Environment separation.

Every environment sets `SITE_TIMEZONE` (an IANA time zone name such as `America/Los_Angeles`; `UTC` when unset) to the
factory's calendar zone; the production stack sets it from `PARTFLOW_SITE_TIMEZONE`. The backend validates it at
startup and uses it, never a browser's local time, to date Work Order completions (and therefore the Done range and the
on-time or late outcome of the completed history) and the default received date of new and imported Work Orders.
Staging and production must use the same value.

## 7. Common deployment flow

Every platform follows the same release order. `deploy/production/release.sh` and `smoke.sh` perform steps 5 to 11
and take the pre-release backup of step 4 (§3.1, Releases); the manual commands and decision points are in
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) §5.

1. Select and record an immutable release tag and commit following §10.
2. Check that CI passed for that exact commit.
3. Read the migration notes from the currently deployed release to the target.
4. Verify the latest backup and take a fresh pre-release backup. `release.sh` takes it, verified, inside the write
   freeze when a migration is pending (step 6), and `migrate` refuses a stale, foreign or tampered backup
   (`deployment/OPERATIONS_RUNBOOK.md` §3 and §5).
5. Build the target images without replacing the running release; `release.sh` builds the candidate and never touches
   the running one.
6. When a migration is pending, enter the write freeze by stopping `backend` (`deployment/OPERATIONS_RUNBOOK.md` §5).
7. Run the migration once and keep its output. `migrate` applies it in one transaction, refuses while `backend` is
   connected and refuses non-transactional schema changes.
8. Switch the application. `backend` starts first on the new release while `web` still serves the previous bundle, so
   the write gate refuses writes from the open pages; after the health check passes, `web` is switched and writes
   reopen. On a database with no Administrator, start the backend with one worker
   (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, so there is a single setup token, §3.1), complete first-run setup
   (the setup token is in the backend log) before opening access, then restart it with the configured worker count
   (`$PF up -d backend`). Then enroll each Scan Station device (Administration → Scan Stations).
9. Run the health, API, user interface, authorization, scan-focus and write-and-read-back smoke checks with designated
   test data.
10. Run the quantity and Movement reconciliation checks. `release.sh` runs a reconciliation before and after the
    release; a failed check after the switch stops `backend` again.
11. Record the deployed release, migration head, operator, time and results (`release.sh` writes `record.json`).
12. Keep the previous release and the pre-release backup until the observation window ends.

## 8. Platform guides

- [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md): staging and production on the company Synology NAS.
- [`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md): the Deployment Admin tool `pf` for staging; its
  §1 records the planned reusable-controller design goal.
- [`deployment/VPS.md`](./deployment/VPS.md): production on a Linux VPS.
- [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md): the conditions for shared hosting.
- [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md): day-to-day operation.

## 9. External platform references

These references describe platform capabilities, not PartFlow readiness:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager) documents multi-container
  Projects from Compose files.
- [Synology Container Manager Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7)
  is the user interface reference for creating and operating a Project.
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  documents Python deployment through `mod_passenger`; it does not by itself prove native ASGI/FastAPI compatibility.
- [Hawk Host remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  confirms that PostgreSQL is available in that environment and that remote access needs whitelisting by the
  provider's support; plan-specific capability must still be confirmed before choosing shared hosting.

## 10. Release and Versioning

This section owns PartFlow's release naming, release notes and publication checklist. A release is a traceable
deployment candidate, not proof of production readiness; it is deployed as §7 describes.

### 10.1 Release identity

Use one application release for the frontend, the backend and the migrations, all from the same commit. Record its Git
tag and full commit SHA. Alembic revisions identify the database schema separately; package metadata is not release
history. The release identity is baked into both production images and reported by `GET /api/health` (`release`,
`commit`) (§3.1, Health and release identity).

Create a release for a version selected for testing or deployment, not for every commit. Redeploying the same version
does not need a new release. Never move, overwrite or reuse a published release tag, or replace its published build
artifacts. Changed release content needs a new version.

Use versioned tags, not `Stage`, `Production`, `Latest` or phase numbers. Existing non-versioned tags may remain as
historical references, but must not become moving deployment targets. The environment and the purpose belong in the
release notes.

### 10.2 Version convention

Use [Semantic Versioning](https://semver.org/spec/v2.0.0.html) with a lowercase `v` prefix for Git tags:
`vMAJOR.MINOR.PATCH`, optionally followed by `-alpha.N`, `-beta.N` or `-rc.N`. Start `N` at 1 and use no leading
zeroes. `release.sh` refuses a production release whose tag does not follow this form.

| Situation | Example |
| --- | --- |
| First internal development snapshot selected for staging | `v0.1.0-alpha.1` |
| Another snapshot of the same planned release | `v0.1.0-alpha.2` |
| A new development milestone with a materially larger scope | `v0.2.0-alpha.1` |
| Intended scope is implemented; broader testing remains | `v0.2.0-beta.1` |
| Candidate for the first stable production release | `v1.0.0-rc.1` |
| First stable production release | `v1.0.0` |
| Compatible bug fix after `v1.0.0` | `v1.0.1` |
| Compatible feature addition after `v1.0.0` | `v1.1.0` |
| Breaking change to the supported contract after `v1.x` | `v2.0.0` |

These are examples, not reserved tags or a mandatory sequence. Choose the next unused version from the actual release
history and the scope of the change. Before 1.0, compatibility is not guaranteed, but breaking changes must still be
documented.

For PartFlow, the supported contract includes the documented API behaviour and the configuration and data-upgrade
requirements. A database migration alone does not require a major increment; assess its compatibility impact.

`alpha`, `beta` and `rc` express release maturity, not the deployment host. Mark all three as GitHub pre-releases and
never designate them as the latest stable release. A stable version may be rehearsed in staging before production.
The suffix never proves that deployment validation passed.

### 10.3 Release title and description

Use `PartFlow <tag>` with an optional short purpose, for example `PartFlow v0.1.0-alpha.1 — Internal Staging`. Keep
release titles and descriptions in English unless explicitly requested otherwise.

For the first release, summarize the implemented scope. For later releases, describe the changes from the selected
previous release to the exact target commit, not just the latest commit or the phase plan. Exclude uncommitted work.

Use this description template, replacing the placeholders with verified information:

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

Never infer "No new migrations", compatibility or successful validation from the release name; check the revision
range and the actual evidence. Unknown results remain `Pending` or `Not verified` in a draft and are never reported as
passed. Keep NAS smoke-test results separate from CI results. Never include secrets, credentials or private deployment
details in public release notes.

### 10.4 Publication checklist

1. Select the exact target commit and the previous release used for comparison. Resolve any local-only changes before
   selecting the published source.
2. Check local and remote tags for collisions. Confirm that the repository CI and the relevant quality checks passed
   for that exact commit; a successful run for another commit does not count.
3. Review the changes, migrations, configuration, rollback requirements and known limitations. Complete the release
   notes.
4. Create an annotated tag at the selected commit, not at an unchecked moving branch tip, and push that specific tag.
5. In GitHub Releases, draft a release from the existing tag. Enter its title and description, set the pre-release
   flag consistently with §10.2, and attach any intended build artifacts before publication.
6. Publish, then deploy using §7 and the platform guide, recording the tag, commit, database revision, operator, time
   and smoke-test results. Keep the previous release and the required backups.

A draft can be prepared before validation finishes. Publishing the release and deploying it are separate operations;
a request to prepare release information authorizes neither.

### 10.5 Current workflow and rollback boundary

`.github/workflows/ci.yml` runs for pushes to `main` and for pull requests. It checks the code, builds the development
images and the production images with throwaway values, and runs the production static tests. It does not publish
release images or deploy anything when a tag or release is created. GitHub source archives are not prebuilt production
images, and a fixed source tag does not guarantee identical later rebuilds when base images can change.

Manual releases are sufficient for this stage. The deployment record holds the local image IDs of every release. When
publishing production images to a registry is implemented, record the registry digests beside the release tag and keep
the deployed artifacts. Do not introduce a separate version service, release branch hierarchy or automatic publisher
only to apply this convention.

Rollback follows the operations runbook: switching application tags does not undo a migration. Confirm schema
compatibility or use the database recovery path, accounting for the writes made after the backup. Never assume that
restoring an older backup preserves newer production Movements.

References: [Git tags](https://git-scm.com/docs/git-tag) and
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Restored rules the rewrite had dropped: the configuration key test, setup-token log access, device credentials and revocation, security updates and the version-suffix caveat. |
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
