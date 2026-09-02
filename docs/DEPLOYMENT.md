# PartFlow Deployment Guide

> **Authority:** Canonical deployment and operations entry point. It describes
> what is deployable now, what Phase 16 must add, and the portable operating
> contract for Synology NAS, VPS, and conditional shared hosting.
>
> **Language:** English is the source of truth. [Tiếng Việt](./vi/DEPLOYMENT.md).

## 1. Roadmap status

PartFlow has a deployment phase: **Phase 16 — Deployment, Production Hardening,
and Admin Maintenance** in `IMPLEMENTATION_ROADMAP.md`. Phase 16 covers backups,
migrations, HTTPS/internal access, observability, rollback, reconciliation,
pilot deployment, and administrative archive/purge maintenance.

At source commit `194ffc2e5e8e22c389abecd0830292a6707955d9`, the repository has
Phases 1–10 implemented end to end (through Phase 10 — Stockroom and
WorkOrderAllocation, backend and frontend); the monitoring read models
(Phase 11), Priority Management (Phase 12), and full Administration
(Phase 13) are still development-only previews or honest unavailable states.
Authentication and role enforcement are Phase 14. Production hardening and
production deployment artifacts are Phase 16.

Therefore:

| Use | Current repository | Decision |
| --- | --- | --- |
| Developer workstation | Supported | Use `compose.yaml` as documented in the root README. |
| Internal Synology staging/test | Supported with restrictions | LAN-only, synthetic/non-production data, controlled users, and explicit backups. See [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Pilot or production use | Not ready | Wait for Phase 14 authorization and the Phase 16 production artifacts and gates in §5. |
| Internet exposure | Prohibited now | The current application has no production authentication boundary and the current Compose stack exposes development services. |

An internal staging deployment does not mean Phase 16 is complete.

## 2. Why the current Compose stack is development-only

The repository itself labels `compose.yaml` and both Dockerfiles as development
artifacts. Observed constraints include:

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
- Phase 14 authentication/role enforcement is not implemented;
- several approved views are still development-only previews or pending real
  backend/frontend integration.

Never hide these limitations behind a NAS reverse proxy or a public DNS name.

## 3. Target portable topology

The Phase 16 production package should keep one topology across Synology and a
future VPS:

```text
Browser / barcode workstation
            |
          HTTPS
            |
Reverse proxy (only public/LAN entry point)
       |                    |
       | /                  | /api
       v                    v
Static frontend         FastAPI backend
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
- Every production view in the intended pilot scope uses real APIs; no mock or
  explicit unconnected placeholder is mistaken for an operational feature.
- Production writes remain blocked while disconnected and are never queued
  locally.
- The full repository quality gates and migration tests pass for the exact
  release commit.

### Production artifacts

- production backend image has no reload server and uses a documented process
  model;
- production frontend is an immutable Vite build served by a production web
  server;
- production Compose configuration has restart policies, health checks,
  private networks, persistent volumes, conservative resource limits, and no
  development bind mounts;
- reverse proxy configuration owns TLS, SPA fallback, request limits, and
  `/api` routing;
- required configuration is validated at startup and secrets have no committed
  defaults;
- image or release versions are immutable and retained long enough to roll back
  application code.

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

1. Select and record an immutable release commit/tag.
2. Confirm CI and release quality gates for that exact revision.
3. Read the migration notes from the currently deployed revision to the target.
4. Verify the latest backup and create a fresh pre-release backup.
5. Build or pull the target images without replacing the running release.
6. Enter the approved maintenance mode/window when required.
7. Run Alembic migration once and capture its output.
8. Start the target application release.
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

