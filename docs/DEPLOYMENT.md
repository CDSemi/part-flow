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

At source commit `d277f8e53a7ca79e0211c211a344dce60e8c7d7f`, the repository has
Phases 1–10 implemented end to end, plus Phase 10.5 — Scan Station Receive
Quantity and the Phase 11 Production Board, Area Board, and PN Tracking
read models and real frontend views. Priority Management (Phase 12) and full
Administration (Phase 13) remain development-only previews or honest
unavailable states. Authentication and role enforcement are Phase 14.
Production hardening and production deployment artifacts are Phase 16.

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

1. Select and record an immutable release commit/tag following §10.
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
