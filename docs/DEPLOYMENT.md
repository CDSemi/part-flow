# PartFlow Deployment Guide

> **Authority:** Canonical deployment and operations entry point. It describes
> what is deployable now, what Phase 16 must add, and the portable operating
> contract for Synology NAS, VPS, and conditional shared hosting.
>
> **Language:** English is the source of truth. [Tiếng Việt](./DEPLOYMENT.vi.md).

## 1. Roadmap status

PartFlow has a deployment phase: **Phase 16 — Deployment, Production Hardening,
and Admin Maintenance** in `IMPLEMENTATION_ROADMAP.md`. Phase 16 covers backups,
migrations, HTTPS/internal access, observability, rollback, reconciliation,
pilot deployment, and administrative archive/purge maintenance.

The repository has Phases 1–15 closed, including Phase 10.5 — Scan Station
Receive Quantity, the Phase 11 monitoring views, Priority Management
(Phase 12), Full Administration (Phase 13), Phase 14 — Authentication, Role
Enforcement, and Authorized Management Corrections (sign-in for application
Users, server-side permission enforcement on every Administration and
Management read and write, and Scan Station routes that require a station
device enrolled by an administrator, §2) and Phase 15 — File-Based Work Order
Import (closed 2026-10-08). Phase 16 is in progress: slice 1 (the read-only
`reconcile` command) and slice 2 (production artifacts: the production
backend and `web` images, `compose.production.yaml`, its configuration and
secret inventory, and network rate limiting, §3.1) are implemented. Phase 16
still owns the release flow, role hardening, backups, observability, host TLS
and the gates (§5 and `IMPLEMENTATION_ROADMAP.md`).

Therefore:

| Use | Current repository | Decision |
| --- | --- | --- |
| Developer workstation | Supported | Use `compose.yaml` as documented in the root README. |
| Internal Synology staging/test | Supported with restrictions | LAN-only, synthetic/non-production data, controlled users, and explicit backups. See [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Pilot or production use | Not ready | Production artifacts exist (§3.1: images, `web`, `compose.production.yaml`, configuration inventory), but the release flow, role hardening, backups, observability and the pilot gates of §5 remain (Phase 16: P16-S3…S7). |
| Internet exposure | Prohibited now | TLS is terminated by the platform proxy, which no host has configured or verified yet (P16-S7), and the §5 gates have not passed. Network rate limiting exists in `web`; `compose.yaml` still exposes development services (§2). |

An internal staging deployment does not mean Phase 16 is complete.

## 2. Why the current Compose stack is development-only

The repository itself labels `compose.yaml` as a development artifact, and the
default (last) `development` stage of each Dockerfile with it; both Dockerfiles
now also hold a `production` stage that Compose does not build (§3.1).
`compose.yaml` is unchanged and remains development-only. Observed constraints
include:

- backend starts Uvicorn with `--reload`;
- frontend runs the Vite development server instead of serving an immutable
  production build;
- source directories and dependency directories are bind-mounted;
- PostgreSQL, backend, and frontend ports are published to the host;
- development credential defaults exist;
- the database and application share the Compose-created PostgreSQL role;
- no production reverse proxy, TLS policy, secret store, log rotation, release
  image tags, scheduled backup job, restore drill, or deployment rollback
  command is provided;
- Phase 14 sign-in for application Users exists and server-side permission checks cover every Administration and Management read and write; every Scan Station route requires a station device enrolled by an administrator for that station and each station action the permission of the role applied at Scan Stations (Phase 14 slice 4) — the network-wide anonymous station access is closed;
- several approved views are still development-only previews or pending real
  backend/frontend integration.

Never hide these limitations behind a NAS reverse proxy or a public DNS name.

**Upgrade step for Phase 14 slice 2 (Administration enforcement).** Before and
after deploying it, count the active users with a password whose role holds
each permission-management key (run it in the database shell, for example
`docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "..."`):

```sql
SELECT rp.permission, count(*) AS holders
FROM users u
JOIN user_credentials c ON c.user_id = u.id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE u.is_active
  AND rp.permission IN ('MANAGE_USERS_AND_ROLES', 'MANAGE_CORRECTION_PERMISSIONS')
GROUP BY rp.permission;
```

A missing row means no holder. Expect both counts to be at least 1, or no
`MANAGE_USERS_AND_ROLES` row at all (first-run setup is then open). A missing
`MANAGE_CORRECTION_PERMISSIONS` row while `MANAGE_USERS_AND_ROLES` has holders
means nobody may manage correction permissions: before deploying, grant it in
Administration; after deploying, run
`docker compose exec backend uv run python -m app.cli restore-correction-permission-management --role-name <role>`
(see `README.md`). The backend also logs a startup warning in that state.

## 3. Target portable topology

The Phase 16 production package should keep one topology across Synology and a
future VPS. TLS ends at the platform proxy (DSM reverse proxy, or Caddy on a
VPS); the in-stack `web` (nginx) serves the immutable build and `/api` and is
published on the host loopback address only (owner decision OD-16-02):

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

- expose only HTTPS (and optionally HTTP solely for redirect) to clients;
- keep PostgreSQL private; never publish port 5432 to an untrusted network;
- keep the backend private when the reverse proxy can route `/api` internally;
- serve frontend and API from one origin so the browser continues to use the
  existing relative `/api` URLs;
- persist PostgreSQL data and backup output outside ephemeral containers;
- run schema migration as an explicit release step, never as an uncontrolled
  side effect of every application replica starting;
- identify every deployment by an immutable Git commit or image tag;
- make the same backup format portable between NAS and VPS.

### 3.1 Production stack (Phase 16 slice 2)

**State.** Implemented (P16-S2): the `production` stages of
`backend/Dockerfile` and `frontend/Dockerfile`, the `web` configuration in
`frontend/nginx/`, the backend database settings below, `compose.production.yaml`
(Compose project `partflow-production`), `.env.production.example`, the
production static tests (`deploy/production/tests/test_production_artifacts.py`)
and the Compose stack smoke (`deploy/production/tests/stack_smoke.py`). Evidence
is Windows/Docker Desktop and a Linux container only; nothing in this section
has been verified on the Synology NAS or a VPS (that is P16-S7), and the
release flow, role hardening, backups and observability remain P16-S3…S6.

**Services and networks (`compose.production.yaml`).**

| Service | Role | Network | Notes |
| --- | --- | --- | --- |
| `db` | PostgreSQL `postgres:16.14` (Debian variant, never `-alpine`: collation and reconcile check (j) depend on glibc) | `internal` (no external route) | volume `postgres_data`; no published port; 60 s stop grace |
| `backend` | image `partflow/backend:${PARTFLOW_RELEASE}`, `production` stage | `internal`, `edge` | `SESSION_COOKIE_SECURE=true` fixed; `WEB_CONCURRENCY` from `PARTFLOW_BACKEND_WORKERS` (default 2); `FORWARDED_ALLOW_IPS` = the edge subnet; 200 s stop grace (above `web`'s longest 180 s upstream timeout); `restart: unless-stopped` (never `on-failure`: with several workers a configuration refusal exits `0`) |
| `web` | image `partflow/web:${PARTFLOW_RELEASE}`, `production` stage | `edge` | the only published port, `127.0.0.1:${PARTFLOW_HTTP_PORT}:80` (no variable for the bind address); no `depends_on`, so it keeps serving the shell while `backend` is stopped |
| `migrate` | one-shot `alembic upgrade head` from the backend image | `internal` | profile `ops`: never started by `up`; run with `--profile ops run --rm` (P16-S3 replaces the command) |

Images are built locally from the checked-out release, through the build-only
companion file `compose.production.build.yaml`, and never pulled
(`pull_policy: never`); the tag is `PARTFLOW_RELEASE`. `compose.production.yaml`
has no build section, so `up` or `run` with a tag whose images are missing fails
with `No such image` instead of building the current checkout under that tag.
Every service has a
restart policy, a health check (except `migrate`), memory and CPU limits from
the environment file (starting values, to be measured on the pilot host in
P16-S7), and `json-file` log rotation (10 MiB, 5 files). Secrets are mounted as
files under `/run/secrets`; no secret is an environment value, and none has a
committed default. Until P16-S4 provisions least-privilege roles, `backend` and
`migrate` use the PostgreSQL bootstrap (owner) role.

**Images.** `backend` (`production` stage): Python 3.12 slim, the locked
non-development dependencies, no `tests/`, no `.env`, no reload server, runs as
user `10001:10001`; start command `uvicorn app.main:app --host 0.0.0.0
--port 8000 --no-access-log`. It never runs migrations on start. `web`
(`production` stage): the pinned official `nginx:1.30.5-alpine` with the
immutable build from `npm run build` (which includes the production-boundary
check). Both default `development` stages are unchanged.

**Backend database configuration.** The backend takes its connection from
exactly one of: `DATABASE_URL` (development, tests, CI, staging), or
`DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` and `DATABASE_PASSWORD_FILE`
plus optional `DATABASE_PORT` (default 5432). Both forms present, or neither
complete, is refused at startup. The password file must hold exactly one line
(trailing line breaks are ignored), because PostgreSQL's `initdb` takes a new
role's password from the first line only; a multi-line, empty, unreadable or
non-UTF-8 file is refused with a message naming the file path and never its
content. No validation error echoes an input value. The URL is composed in the
application, so special characters in the password need no manual encoding.

**Process model.** `WEB_CONCURRENCY` sets the number of uvicorn workers and
`FORWARDED_ALLOW_IPS` the proxy addresses uvicorn trusts for forwarded headers;
Compose sets both inside the container (`WEB_CONCURRENCY` from
`PARTFLOW_BACKEND_WORKERS`), so a `WEB_CONCURRENCY` value in the operator's
shell has no effect. Each worker has its own first-run setup token and its own
password-hashing bounds, so **first-run setup runs with one worker**
(`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`) and the configured count is
restored afterwards (`$PF up -d backend`); with two workers a request can
reach the worker whose token was not copied and is refused `403
setup_token_invalid` (no write). With more than one worker, a configuration
refusal at startup stops the container with exit code `0`; the log line and the
restart count, not the exit code, are the failure signal. Stopping or
recreating a backend with more than one worker does not refuse new connections
at once: the uvicorn supervisor keeps the listening socket open until its last
worker has exited (up to the 200 s stop grace while an import finishes), so a
request sent meanwhile is accepted but never answered. It ends as `web`'s 504
after the 60 s read timeout, or as a 502 when the supervisor exits; the backend
never ran it, but the client must treat it as an unknown outcome. With one
worker such a request is refused at once (502). Start a write freeze when no
import is running.

**Request limits and timeouts (`web`).**

| Route | Body limit | Upstream timeout |
| --- | --- | --- |
| Default for every `/api` route | 1 MiB | 60 s |
| `PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image` | 4 MiB | 60 s |
| `POST /api/work-orders/import/preview` | 4 MiB | 60 s |
| `POST /api/work-orders/import` | 4 MiB | 180 s (read and send) |

The 4 MiB limit is above the application's own limits (2 MiB images, 1 MiB
import files), so the application's JSON 413 wins for an oversized but
plausible file; `web` answers first only for a clearly larger body. `web` never
retries a request upstream: one client request is at most one backend
execution.

**Rate limits (`web`, per forwarded client IP).** `POST /api/session` 10 per
minute with burst 5 (six attempts at once, then one more every 6 s); `PUT
/api/session/password`, `POST /api/setup/administrator` and `PUT
/api/users/{user_id}/password` 5 per minute with burst 4 (five at once, then
one every 12 s). Reads such as `GET /api/session` and sign-out are never
limited. A 429 from `web` never reaches the application, so it never counts as
a failed sign-in and never locks an account; the per-account lockout and the
per-IP limit are independent layers. Device-activation codes are not rate
limited (50 bits of entropy, 15-minute lifetime).

**Responses generated by `web`.** These appear only for conditions `web`
detects itself; every backend response, including the application's own 401,
403, 409, 413, 422 and 503, passes through unchanged.

| Status | When | Body `detail` | Client meaning |
| --- | --- | --- | --- |
| 413 | body over the location's limit | `This request is too large for PartFlow. Nothing was changed.` (`request_too_large: true`) | definite refusal |
| 429 | rate limit exceeded (sends `Retry-After: 60`) | `Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.` (`rate_limited: true`) | definite refusal |
| 502 | backend unreachable, or it closed the connection before answering | `The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | unknown outcome for a write |
| 504 | no backend answer within the timeout | `The PartFlow server did not answer in time. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | unknown outcome for a write |

A 502 or 504 keeps its 5xx status, so a station write that may have committed
is shown as unknown and retried with the same `device_event_id`
(`deployment/OPERATIONS_RUNBOOK.md` §2). The frontend shows a built-in message
for a 413 or 429 that carries no JSON `detail` (for example one from a platform
proxy). `web` adds no CORS headers and rewrites nothing else.

**Caching and headers.** `index.html` and every single-page-app fallback are
`no-cache`; `/assets/*` are immutable for one year, and a missing asset is a
plain 404, never the application shell. Every response carries
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and a
baseline `Content-Security-Policy` (same-origin scripts, styles, connections
and frames; `blob:` and `data:` images for the local image previews; no inline
script). Whoever adds a feature that needs another source amends the policy in
the same change. HSTS belongs to the platform proxy and is reviewed in P16-S7.

**Real client address.** `web` trusts forwarded headers from exactly one hop,
detected when the container starts as its default gateway on the edge network
(the address host-loopback connections arrive from), or set explicitly with
`PARTFLOW_TRUSTED_PROXY` (one IPv4 address). If neither yields an address the
container refuses to start. From that hop `web` takes the **last**
`X-Forwarded-For` address as the client and replaces the header it sends the
backend with that single address; `X-Forwarded-Proto` is honoured only from the
same hop. `web` publishes no certificate and reads no TLS configuration.

**Request log.** The `web` access log (client address, method, path without
query string, status, bytes, duration, user agent) is the request log in this
slice; it never contains cookies, query strings or any PartFlow header, and the
health probes are excluded. uvicorn's own access log is off.

**Platform proxy requirements.** The DSM reverse proxy (or Caddy) must:
terminate HTTPS with a certificate that company workstations trust; send HTTP
only as a redirect to HTTPS; forward to `http://127.0.0.1:<port>` (the literal
`127.0.0.1`, never `localhost`, which can resolve to `::1` first while `web` is
published on IPv4 loopback only) with `Host`, `X-Forwarded-For` (the client
address appended last) and `X-Forwarded-Proto: https`; accept request bodies of
at least 5 MiB and use send and read timeouts of at least 300 s, so that `web`'s
or the application's JSON answer wins; never log cookies,
`X-PartFlow-Station-Device` or `X-PartFlow-CSRF`; and admit only the approved
LAN or VPN sources, together with the host firewall. If the proxy cannot supply
the client address, every client shares one rate-limit bucket; record it and let
the owner decide. DSM settings are in
[`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §5 and the Caddy
example in [`deployment/VPS.md`](./deployment/VPS.md) §4. These requirements are
documented here; the host settings are **executed and verified in P16-S7**.

**Certificate procedure.** The certificate names the PartFlow hostname (SAN)
and is issued by a public ACME CA (public DNS name) or by the company's internal
CA (internal-only name); never self-signed per host and never accepted
workstation by workstation past a browser warning. The issuing CA of an internal
certificate is distributed to workstations and barcode terminals by the
company's device management. The deployment administrator owns renewal; expiry
monitoring is P16-S6. Check expiry from any client: `openssl s_client -connect
<host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout
-subject -enddate`. Platform steps are in SYNOLOGY_NAS §5 and VPS §4. Executed
and verified in P16-S7.

**Environment separation (OD-16-01).** Production uses its own Compose project,
database volume, secrets directory, hostname and (P16-S5) backup location; none
is shared with or pointed at a staging or development stack. Production starts
from a new, empty database volume, then migrations, then first-run setup;
staging or development data is never attached, reused or copied into it, and a
deliberate restore into production is a P16-S5 procedure that needs an owner
decision. During the pilot, pf-managed staging is not installed on the pilot
Docker daemon, and the manual `compose.yaml` staging of SYNOLOGY_NAS §4 is
stopped, with its containers removed and **without** deleting volumes, before
production starts. `SITE_TIMEZONE` equals the staging value (§6). Never run
`down -v` (or remove a volume) on the production project: it deletes the
database.

**Configuration and secret inventory.** `.env.production` (copied from
`.env.production.example`, git-ignored, mode 600) holds only non-secret values;
the set of `${NAME}` references in `compose.production.yaml` equals the set of
keys in the example (tested).

| Key | Meaning | Required / default |
| --- | --- | --- |
| `PARTFLOW_RELEASE` | tag of the **running** images (§10) | required |
| `PARTFLOW_SECRETS_DIR` | absolute path of the secrets directory, outside the checkout, production only (directory 0700, each file 0444) | required |
| `PARTFLOW_SITE_TIMEZONE` | factory calendar zone, equal to staging (§6) | required |
| `PARTFLOW_HTTP_PORT` | loopback port the platform proxy connects to | required (example `18080`) |
| `POSTGRES_USER`, `POSTGRES_DB` | bootstrap (owner) role and database | required (example `partflow_owner`, `partflow`) |
| `PARTFLOW_BACKEND_WORKERS` | uvicorn workers | `2` |
| `PARTFLOW_EDGE_SUBNET` | subnet of the `edge` network; also the uvicorn trusted proxies | `172.30.250.0/24` |
| `PARTFLOW_TRUSTED_PROXY` | one IPv4 address `web` trusts for forwarded headers; empty = auto-detect | empty |
| `PARTFLOW_{DB,BACKEND,WEB,OPS}_{MEMORY,CPUS}` | resource limits | `1g`/`1.0`, `1g`/`2.0`, `128m`/`0.5`, `512m`/`1.0` |

Fixed in Compose, not configurable: `SESSION_COOKIE_SECURE=true`,
`DATABASE_HOST=db`, `DATABASE_PORT=5432`, the secret mount paths and the
loopback bind. The one secret file in this slice is `postgres_password`
(exactly one line). `db` reads it **only when a new data volume is
initialized**; `backend` and `migrate` read it at every start. To change it on
an existing database, run `ALTER ROLE <POSTGRES_USER> PASSWORD …` first, then
replace the file and recreate `backend`; replacing the file alone breaks the
backend's login. P16-S4 adds the application and maintenance role passwords to
the same directory.

**Operator commands.** Run from the release checkout, with
`PF="docker compose -f compose.production.yaml --env-file .env.production"`.

| Purpose | Command |
| --- | --- |
| Preflight (§6, environment separation) | `docker ps -a --format '{{.Label "com.docker.compose.project"}}' \| sort -u` lists no `partflow-staging` |
| Validate the configuration | `$PF config --quiet` |
| Build a release (tag in the shell; the only use of the build file) | `PARTFLOW_RELEASE=<new> $PF -f compose.production.build.yaml build` |
| Start the database | `$PF up -d db` |
| Apply migrations (once per release) | `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm migrate` (first install: the tag is already in `.env.production`) |
| Start or recreate the application | `$PF up -d backend web` |
| Status and logs | `$PF ps` · `$PF logs --since=15m backend web db` |
| Health through `web` on the host | `curl --fail --silent --show-error http://127.0.0.1:${PARTFLOW_HTTP_PORT}/api/health` |
| Reconciliation (also while `backend` is stopped) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile` |
| Identity rehearsal of a candidate image (check (j)) | `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j` |
| Recovery CLIs | `$PF run --rm --no-deps backend python -m app.cli reset-password …` · `… restore-correction-permission-management …` |
| Write freeze | `$PF stop backend` (may wait up to 200 s while an import finishes, and requests sent meanwhile hang up to 60 s, see Process model: start it when no import is running) · reopen with `$PF up -d backend` |
| First-run setup | `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, read the token with `$PF logs backend \| grep "Setup token"`, complete setup, then `$PF up -d backend` |

**Release sequence (manual form; P16-S3 automates it).** `.env.production`
always names the release that is **running**; the candidate tag lives only in
the shell until the switch, so every recovery CLI, reconcile or `up` before the
switch uses the current image against the current schema.

1. With the current tag: the pre-maintenance reconcile and any recovery CLI
   (`deployment/OPERATIONS_RUNBOOK.md` §5).
2. `PARTFLOW_RELEASE=<new> $PF -f compose.production.build.yaml build` (never an existing tag).
3. `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`:
   the candidate image against the unchanged database. A failing check stops
   the release here; nothing has changed.
4. Write freeze (`$PF stop backend`), then the backup step of the runbook.
5. `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm migrate`.
6. Write `PARTFLOW_RELEASE=<new>` into `.env.production`, `$PF up -d backend web`,
   then health and reconcile.

Rollback of application code with no schema change: restore the previous tag in
`.env.production` and `$PF up -d backend web`. It needs the previous release's
images on the host, so keep them (no `docker image prune -a`) through the
rollback window; when they are gone, `up` fails with `No such image` and starts
nothing. A schema rollback is the restore
path of the runbook (P16-S3/S5), never a down-migration here. **Never run
`down -v`, or remove a volume, on the `partflow-production` project**: it
deletes `partflow-production_postgres_data`; `$PF down` without `-v` is the
only stop-everything form.

**Evidence.** 29 static tests in `deploy/production/tests` (Compose model,
environment example, Dockerfiles and the nginx configuration; run by CI and
checked against 21 deliberate mutations of the artifacts), the production image
builds (the `web` build runs the production-boundary check: 59 assets, none of
the 11 mock sentinels), and a Compose stack smoke on Docker Desktop (cases
SM-1…SM-22, including the rate limit, the proxy-generated JSON answers, the
content security policy in a real browser, the one-worker first-run, the
reconcile commands, and 2,000-Work-Order import timings of 18.06 s to create and
31.33 s to change quantities, below `web`'s 180 s). Host checks on the Synology
NAS and a VPS are not part of this evidence.

## 4. Platform decision

| Platform | Fit | Recommended role |
| --- | --- | --- |
| Synology NAS with Container Manager | Good for a small internal deployment when the model supports the required containers and the NAS has reliable storage, memory, monitoring, UPS coverage, and tested backups | Internal staging now; pilot/production only after Phase 16 gates pass |
| Linux VPS | Best long-term portable target | Preferred upgrade path for production, remote access, predictable Docker control, and independent off-site recovery |
| Shared hosting such as Hawk Host | Conditional and not drop-in | Use only if the provider proves native ASGI/FastAPI process support, PostgreSQL, required routing, migrations, jobs, and recovery controls; otherwise choose a VPS |

Moving from Synology to VPS should be a release redeployment plus a verified
PostgreSQL dump/restore, not an application rewrite.

## 5. Production release gates

PartFlow may enter pilot/production only when all gates below are satisfied.

### Application and authorization

- Phase 14 authentication and server-side role enforcement are complete and
  tested; hiding navigation is never authorization.
- Every Scan Station device is enrolled, its name recorded, and lost or retired
  devices are revoked. Enrollment codes and device tokens are bearer credentials:
  like session cookies they travel only over HTTPS or the formally accepted
  isolated LAN (Network and host below), and the reverse proxy never logs the
  `X-PartFlow-Station-Device` header.
- `SESSION_COOKIE_SECURE=true` is set behind TLS. The first-run setup token
  is the only secret ever written to the backend log: complete first-run
  setup before exposing the service and restrict log access until then.
- Every production view in the intended pilot scope uses real APIs; no mock or
  explicit unconnected placeholder is mistaken for an operational feature.
- Production writes remain blocked while disconnected and are never queued
  locally.
- The full repository quality gates and migration tests pass for the exact
  release commit.

### Production artifacts

- production backend image has no reload server and uses a documented process
  model (each backend process announces its own first-run setup token while no
  Administrator exists; the first creation closes setup for all of them) —
  **implemented** (`backend/Dockerfile` `production` stage; process model in
  §3.1);
- production frontend is an immutable Vite build served by a production web
  server — **implemented** (`web`, §3.1);
- production Compose configuration has restart policies, health checks,
  private networks, persistent volumes, conservative resource limits, and no
  development bind mounts — **implemented** (`compose.production.yaml`, §3.1;
  resource limits are starting values measured in P16-S7);
- reverse proxy configuration owns TLS, SPA fallback, request limits, and
  `/api` routing — the proxy must accept request bodies of at least 3 MiB on the
  image upload routes (`PUT /api/workers/{id}/avatar`,
  `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image?number=…`), because the application accepts images up to 2 MiB; a
  proxy-generated 413 carries no JSON `detail`, so the UI could only show a
  generic failure; likewise the proxy must accept request bodies of at least
  2 MiB on the Work Order import routes (`POST /api/work-orders/import/preview`
  and `POST /api/work-orders/import`), because the application accepts files up
  to 1 MiB and its JSON 413 must win, and the read timeout on
  `POST /api/work-orders/import` must exceed the measured worst case — a
  development-environment run of the largest allowed file (2,000 single-line
  Work Orders, one transaction each) took 26.03 s to import (0.3 s to check,
  7.48 s to replay as already imported), and a run in which every one of those
  Work Orders changes a quantity (PF-2) took 27.23 s to import (2.45 s to
  check; replaying the same file, now all as saved, took 2.48 s to check and
  2.45 s to import); a read timeout of at least
  120 s is still recommended. `web` implements these limits with the values in
  §3.1 (4 MiB on those five routes, 1 MiB elsewhere, a 180 s import read
  timeout) — **implemented**; the platform proxy in front of it must accept at
  least 5 MiB and use timeouts of at least 300 s, and TLS is terminated there —
  documented in §3.1, **executed and verified in P16-S7**;
- required configuration is validated at startup and secrets have no committed
  defaults — **implemented** (§3.1: the database settings are validated by the
  backend; Compose fixes the cookie setting and requires the time zone and the
  secrets directory);
- image or release versions are immutable and retained long enough to roll back
  application code — **partly implemented** (images are tagged by
  `PARTFLOW_RELEASE` and never pulled; the release identity inside the image and
  the retention rule are P16-S3).

The gates above remain gates until P16-S7 records passing evidence.

### Data safety and operations

- automated PostgreSQL logical backups run on a documented schedule, are
  encrypted off-host/off-NAS, have retention, and are monitored;
- a restore into an isolated database has been tested and timed;
- every migration has a backup, forward plan, compatibility assessment, smoke
  test, and recovery plan;
- rollback uses the previous compatible application release, or restores the
  matching pre-migration database when a schema rollback is unsafe;
- health, logs, disk use, backup age, database growth, and container restarts
  are monitored;
- movement/quantity reconciliation checks run and alert without mutating data;
- a database that ran the unreleased Phase 12 commits (`80f7925` … `b9785d2`)
  passes this read-only check before the Hot list is relied on — it must return
  0 rows, otherwise the listed inactive Hot entries are removed in Management →
  Priority (the automatic removal of `IMPLEMENTATION_ROADMAP.md` Phase 12 only
  covers changes made after it):
  `SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity);`
  This query is check (i) of the read-only `reconcile` command
  (`deployment/OPERATIONS_RUNBOOK.md` §7).
- an incident owner, maintenance window, RPO, and RTO are explicitly approved;
- pilot entry, pilot exit, and escalation criteria are documented.

### Network and host

- clients use HTTPS or a formally accepted isolated-LAN exception;
- firewall rules allow only required sources and ports;
- DSM/VPS, Container Manager/Docker, and base images receive controlled security
  updates;
- NAS/VPS time synchronization is correct;
- the host has UPS coverage or a documented power-loss strategy;
- capacity alerts leave enough disk headroom for PostgreSQL, image updates,
  temporary migration space, and backups.

## 6. Environment separation

Use separate databases, secrets, URLs, and backup locations for:

- `development` — developer data only;
- `staging` — synthetic or sanitized data, release rehearsal;
- `production` — authorized factory data.

Never restore production data into development without explicit authorization
and sanitization. Never point staging and production at the same database.

Every environment sets `SITE_TIMEZONE` (an IANA zone name; `UTC` when unset) to
the factory's calendar zone: the backend validates it at startup and derives
the done date of a completed Work Order — and therefore the Done range and the
on time / late outcome of the completed history — from it, never from a
browser's local time. Staging and production must use the same value.

## 7. Common deployment flow

Every platform follows the same release order:

1. Select and record an immutable release commit/tag following §10.
2. Confirm CI and release quality gates for that exact revision.
3. Read the migration notes from the currently deployed revision to the target.
4. Verify the latest backup and create a fresh pre-release backup.
5. Build or pull the target images without replacing the running release.
6. Enter the approved maintenance mode/window when required.
7. Run Alembic migration once and capture its output.
8. Start the target application release. On a database with no
   Administrator, start the backend with one worker
   (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, so there is a single setup
   token, §3.1), complete first-run setup (the setup token is in the backend
   log) before opening access, then restart it with the configured worker count
   (`$PF up -d backend`). Then enroll each Scan Station device
   (Administration → Scan Stations).
9. Run health, API, UI, authorization, scan-focus, and write/read-back smoke
   checks using designated test data.
10. Run quantity/movement reconciliation checks.
11. Record the deployed revision, migration head, operator, time, and results.
12. Keep the previous release and pre-release backup until the observation
    window ends.

Detailed commands and decision points are in
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md).

## 8. Platform guides

- [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md)
- [`deployment/VPS.md`](./deployment/VPS.md)
- [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md)
- [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md)

## 9. External platform references

These references describe platform capabilities, not PartFlow readiness:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager)
  documents multi-container Projects from Compose files.
- [Synology Container Manager Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7)
  is the UI reference for creating and operating a Project.
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  documents Python deployment through `mod_passenger`; it does not by itself
  prove native ASGI/FastAPI compatibility.
- [Hawk Host remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  confirms PostgreSQL is available in that environment and that remote access
  requires support-side whitelisting; plan-specific capability must still be
  confirmed before selecting shared hosting.

## 10. Release and Versioning

This section owns PartFlow's release naming, release notes, and publication
checklist. A release is a traceable deployment candidate, not proof of
production readiness. The gates in §5 still apply to pilot/production.

### 10.1 Release identity

Use one application release for the frontend, backend, and migrations from
the same commit. Record its Git tag and full commit SHA. Alembic revisions
identify the database schema separately; package metadata is not release
history.

Create a release for a version selected for testing or deployment, not for
every commit. Redeploying the same version does not require a new release.
Never move, overwrite, or reuse a published release tag, or replace its
published build artifacts. Changed release content requires a new version.

Use versioned tags, not `Stage`, `Production`, `Latest`, or phase numbers.
Existing non-versioned tags can remain historical references, but must not
become moving deployment targets. Environment and phase belong in the notes.

### 10.2 Version convention

Use [Semantic Versioning](https://semver.org/spec/v2.0.0.html), with a lowercase
`v` prefix for Git tags: `vMAJOR.MINOR.PATCH`, optionally followed by
`-alpha.N`, `-beta.N`, or `-rc.N`. Start `N` at 1; do not use leading zeroes.

| Situation | Example |
| --- | --- |
| First internal development snapshot selected for staging | `v0.1.0-alpha.1` |
| Another snapshot of the same planned release | `v0.1.0-alpha.2` |
| A new development milestone with a materially larger scope | `v0.2.0-alpha.1` |
| Intended scope is implemented; broader testing remains | `v0.2.0-beta.1` |
| Candidate for the first stable production release | `v1.0.0-rc.1` |
| First accepted stable production release, after §5 passes | `v1.0.0` |
| Compatible bug fix after `v1.0.0` | `v1.0.1` |
| Compatible feature addition after `v1.0.0` | `v1.1.0` |
| Breaking change to the supported contract after `v1.x` | `v2.0.0` |

These are examples, not reserved tags or a mandatory sequence. Choose the next
unused version from the actual release history and change scope. Before 1.0,
compatibility is not guaranteed; breaking changes must still be documented.

For PartFlow, the supported contract includes documented API behavior and
configuration/data-upgrade requirements. A database migration alone does not
require a major increment; assess its compatibility impact.

`alpha`, `beta`, and `rc` express release maturity, not the deployment host.
Mark all three as GitHub pre-releases; do not designate them as the latest
stable release. A stable version may be rehearsed in staging before production.
The suffix never waives §5 or proves that deployment validation passed.

### 10.3 Release title and description

Use `PartFlow <tag>` with an optional short purpose:
`PartFlow v0.1.0-alpha.1 — Internal Staging`. Keep release titles and descriptions
in English unless explicitly requested otherwise.

For the first release, summarize implemented scope. For later releases, describe
the changes from the selected previous release to the exact target commit,
not just the latest commit or the phase plan. Exclude uncommitted work.

Use this Description template, replacing placeholders with verified information:

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

Do not infer "No new migrations", compatibility, or successful validation from
the release name. Check the revision range and actual evidence. Unknown results
remain `Pending` or `Not verified` in a draft; they cannot satisfy a release
gate. Keep NAS smoke-test results separate from CI results. Never include
secrets, credentials, or private deployment details in public release notes.

### 10.4 Publication checklist

1. Select the exact target commit and previous release used for comparison.
   Resolve any local-only changes before selecting the published source.
2. Check local and remote tags for collisions. Confirm the repository CI and
   relevant quality gates passed for that exact commit; a successful run for
   another revision does not count.
3. Review changes, migrations, configuration, rollback requirements, and known
   limitations. Complete the release notes; apply §5 for pilot/production.
4. Create an annotated tag at the selected commit, not at an unchecked moving
   branch tip, and push that specific tag.
5. In GitHub Releases, draft a release from the existing tag. Enter its title
   and description, set the pre-release flag consistently with §10.2, and
   attach any intended build artifacts before publication.
6. Publish when the applicable gates are satisfied. Deploy using §7 and the
   platform runbook, recording the tag, SHA, database revision, operator, time,
   and smoke-test results. Keep the previous release and required backups.

A draft can be prepared before validation finishes. Publishing the release and
deploying it are separate operations; neither is authorized merely by a request
to prepare release information.

### 10.5 Current workflow and rollback boundary

At the source revision in §1, `.github/workflows/ci.yml` runs for pushes to
`main` and pull requests. It checks code and builds development images; it does
not publish release images or deploy to Synology when a tag/release is created.
GitHub source archives are not prebuilt production images. A fixed source tag
also does not guarantee identical later rebuilds when base images can change.

Manual releases are sufficient for this stage. When production image publication
is implemented, record the image digests alongside the release tag and retain
the deployed artifacts. Do not introduce a separate version service, release
branch hierarchy, or automatic publisher solely to apply this convention.

Rollback follows the operations runbook: switching application tags does not
undo a migration. Confirm schema compatibility or use the approved database
recovery plan, accounting for writes after the backup. Never assume restoring
an older backup preserves newer production Movements.

References: [Git tags](https://git-scm.com/docs/git-tag) and
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
