# PartFlow NAS Admin v2.5

> **English is the source of truth.** [Vietnamese translation](./SYNOLOGY_ADMIN.vi.md).
>
> Version: **2.5.0**
> Prepared: **2026-09-11**
> Scope: restricted-LAN **Synology staging** administration. This is not a production-hardening package.

> **Deployment Admin checkpoint PF-A1.1 (2026-09-14) — development state, not a NAS release.**
> The controller source in this repository now requires a *protected instance registration*
> (`pf_instance.py`: installation root with `bootstrap/`, `registry/instances.json`,
> `locks/`, `releases/<id>/`, `instances/<uuid>/record.json`). Construction, `--help`,
> `instances`, `status`, `backups`, `recoveries` and the default `doctor` are read-only;
> `status`/`doctor` print the pending journal before any `.env`, Git or Docker check.
> Mutating commands need `--instance <slug|uuid>` (or a protected default), the stable
> per-instance lock under `<root>/locks/` and an explicit journal route. The v2.5 layout
> described below is **unregistered** in this checkpoint: the installed `control/pf.sh`
> answers read-only diagnostics only and refuses mutations until the PF-A2 migration
> exists. `install-control.sh` remains the legacy v2.5 installer; do not run it against a
> live NAS with this checkpoint. Registration exists only as a Python transaction for
> disposable fixtures (`register_instance`), not as an operator command.

## 1. Purpose

PartFlow NAS Admin separates the writable application repository from the privileged
lifecycle controller. This allows trusted DSM users to edit the repository over SMB
without making the code executed by `sudo pf ...` writable by those users.

The operational layout is:

```text
/volume1/docker/partflow/
├── repo/                              # application working tree; users read/write/delete
├── control/                           # installed lifecycle control plane; root modifies
│   ├── pf.sh
│   ├── pf-admin.py
│   ├── compose.nas.yaml
│   ├── backup.sh
│   ├── release-check.sh
│   ├── pf-config.example.json
│   └── nas.env.example
├── config/                            # host/runtime configuration; trusted users may edit
│   ├── .env
│   └── pf-config.json
├── backups/                           # revision checkpoints; users read/copy only
├── recovery/                          # purge/control-upgrade recovery; users read/copy only
└── .pf-state-<project>/               # locks/journal/image state; root only
```

The repository still contains source/reference copies of `pf.sh`, `compose.nas.yaml`,
and `deploy/synology/*` so the control plane remains versioned and reviewable. Those
repository copies are **not** used for normal NAS administration after installation.

## 2. Permission model

Default DSM groups are `users` for repository/config access and backup reading.
`pf-config.json` can change these group names if a dedicated trusted group is preferred.

| Path | Typical mode | Access policy |
| --- | --- | --- |
| `repo/` directories | `2770` | owner + `workspace_write_group` full read/write/delete; setgid preserves group |
| `repo/` regular files | `0660` | owner + `workspace_write_group` read/write |
| `repo/` existing executable files | `0770` | source/working-tree executability only; not the privileged control plane |
| `control/` directories | `0750` | root modifies; `users` can browse/read |
| `control/pf.sh`, `backup.sh`, `release-check.sh` | `0740` | root executes/modifies; `users` read only |
| other `control/` files | `0640` | root modifies; `users` read only |
| `config/` directory | `2770` | trusted `users` may create/edit/delete host config |
| `config/.env`, `config/pf-config.json` | `0660` | trusted `users` read/write |
| `backups/`, `recovery/` directories | `0750` | configured read group can browse/copy, not modify/delete |
| backup/recovery files | `0640` | configured read group can read/copy, not write |
| `.pf-state-*` | `0700` | root only |

DSM Shared Folder ACLs still apply. The DSM account must also have **Read/Write** access
to the shared folder containing `repo/` if SMB editing is expected. POSIX mode bits do
not override a DSM ACL deny.

Run the managed permission repair at any time:

```sh
sudo pf permissions
```

### Trust consequence

Giving `users` write access to `repo/` and `config/` is deliberate for this installation.
A trusted user can therefore change application source and runtime settings, including
the PostgreSQL password stored in `config/.env`. Backup/recovery bundles can also expose
application data and, for purge recovery, a copy of `.env`. Only grant SMB access to
users who are permitted to see and modify this information.

## 3. Does moving `.env` outside the repository affect the app?

No, not when PartFlow is run through the installed controller.

The application does not care where the host-side `.env` file is stored. The controller
explicitly gives Docker Compose the external file:

```text
--env-file /volume1/docker/partflow/config/.env
```

It also explicitly provides the repository path used for image build contexts:

```text
PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo
```

The installed `control/compose.nas.yaml` uses that value:

```yaml
backend:
  build:
    context: "${PARTFLOW_REPO_ROOT}/backend"

frontend:
  build:
    context: "${PARTFLOW_REPO_ROOT}/frontend"
```

Container environment values remain the same as before. Only the **host storage location**
of the file changes.

This separation also prevents `.env` from being accidentally added to the Git working
tree and allows `repo/` to be replaced cleanly during an update without touching runtime
credentials.

The operational rule is therefore:

```sh
sudo pf ...
```

Do not assume a raw `docker compose` command run from `repo/` will automatically discover
the external `.env` or the installed Compose file. See §14 for the explicit advanced form.

## 4. Source files versus installed control files

The repository contains these version-controlled sources:

```text
repo/
├── pf.sh
├── compose.nas.yaml
└── deploy/synology/
    ├── install-control.sh
    ├── pf-admin.py
    ├── backup.sh
    ├── release-check.sh
    ├── pf-config.example.json
    ├── nas.env.example
    ├── TEST_REPORT.md
    └── tests/
```

`install-control.sh` copies the reviewed lifecycle sources to `control/`, changes them to
root-owned/read-only-to-users permissions, and installs a small root-owned launcher:

```text
/usr/local/bin/pf
```

After installation, the repository `pf.sh` intentionally refuses operational execution.
Use:

```sh
sudo pf status
```

not:

```sh
sudo sh ./pf.sh status
```

Application updates do **not** silently update the privileged control plane. If a later
PartFlow revision changes `pf-admin.py`, `compose.nas.yaml`, or another lifecycle source,
review that revision and explicitly reinstall the control plane.

## 5. Install or migrate from Admin v2.4.x

Run this from the repository root:

```sh
cd /volume1/docker/partflow/repo
```

Before running a root installer from a users-writable working tree, verify that the source
revision is one you trust. At minimum inspect the changed deployment files and Git status.
The installer itself is intentionally explicit because installing it grants the reviewed
code lifecycle authority on the NAS.

Then run:

```sh
sudo sh ./deploy/synology/install-control.sh
```

It shows the target paths and requires this exact confirmation:

```text
INSTALL CONTROL
```

The installer performs these migrations safely:

1. Creates `/volume1/docker/partflow/config/`.
2. Moves old `repo/.env` to `config/.env` when present.
3. Moves/copies old `deploy/synology/pf-config.json` into `config/pf-config.json`.
4. Refuses installation if old and new copies of `.env` or `pf-config.json` both exist and differ.
5. Archives an existing `control/` under `recovery/control-upgrades/` before replacement.
6. Installs a new root-owned `control/` copy.
7. Installs `/usr/local/bin/pf` when that path is free or already PartFlow-managed.
8. Runs `pf permissions` to normalize repository/config/backup/recovery modes.

The installer preserves Docker containers, volumes, databases, revision backups, and
application source. It is not a redeploy or database reset.

Validate afterward:

```sh
sudo pf doctor
sudo pf status
```

If `/usr/local/bin/pf` could not be installed because an unrelated file already uses that
name, run the installed launcher directly:

```sh
sudo /volume1/docker/partflow/control/pf.sh status
```

## 6. Configuration files

### `config/pf-config.json`

This is the actual NAS-local administration configuration. It is created from the
root-owned `control/pf-config.example.json` if absent.

Default template:

```json
{
  "repository": "CDSemi/part-flow",
  "branch": "main",
  "project": "partflow-staging",
  "environment": "staging",
  "release_channel": "stable",
  "auto_update": false,
  "ci_workflow": "ci.yml",
  "health_timeout_seconds": 180,
  "minimum_free_mb": 2048,
  "backup_read_group": "users",
  "workspace_write_group": "users"
}
```

Trusted users may edit this file over SMB. The controller validates supported keys,
project naming, booleans, positive numeric values, and configured DSM groups before using it.

### `config/.env`

For a brand-new deployment, `deploy` creates it interactively from the installed
`control/nas.env.example` template. It generates a 64-character hexadecimal
`POSTGRES_PASSWORD` using cryptographically secure randomness and does not print the
password to the terminal.

Typical content:

```dotenv
POSTGRES_USER=partflow_staging
POSTGRES_PASSWORD=<generated-secret>
POSTGRES_DB=partflow_staging
SITE_TIMEZONE=America/Los_Angeles
PARTFLOW_BIND_IP=192.168.0.11
PARTFLOW_HTTP_PORT=5173
PARTFLOW_ALLOWED_HOST=localhost
```

Changing `POSTGRES_USER`, `POSTGRES_PASSWORD`, or `POSTGRES_DB` after PostgreSQL has
already initialized is **not** equivalent to changing the existing database credentials.
Do not casually edit those values on a live instance. Use the managed deployment/recovery
workflow or plan a credential/database migration explicitly.

## 7. First deployment

For a brand-new staging instance, the usual command is:

```sh
sudo pf deploy --latest
```

Other source selectors:

```sh
sudo pf deploy                         # current clean Git checkout
sudo pf deploy --commit FULL_SHA
sudo pf deploy --release TAG
sudo pf deploy --release latest --channel prerelease
```

The new-deploy flow:

1. Verifies this Compose project has no managed deployment record, containers, or volumes.
2. Creates/reuses `config/.env`.
3. Prompts only for deployment-specific values that cannot be safely inferred.
4. Resolves the chosen source to an exact commit SHA.
5. Verifies CI unless `--skip-ci` is explicitly used for manual staging.
6. Builds backend/frontend candidate images before creating the database.
7. Requires `DEPLOY <SHA12>` confirmation.
8. Starts PostgreSQL and confirms it is new/uninitialized.
9. Runs `alembic upgrade head`.
10. Starts backend and verifies health/schema.
11. Starts frontend and verifies `/api/health` through the frontend proxy.
12. Writes the deployed revision to external `.pf-state-<project>/deployed.json`.

After UI/workflow/firewall smoke testing, create the first rollback baseline:

```sh
sudo pf backup
```

If initial deployment fails before frontend access could have opened:

```sh
sudo pf abort-deploy
```

The command requires confirmation and removes only resources from that incomplete first
deployment. It keeps the repository and `config/.env` so deployment can be retried.

## 8. Editable repository and deployed revision

`repo/` is now a working tree, not the authoritative record of what is currently running.
The running application uses previously built/pinned images; editing a source file over SMB
does **not** immediately change the running application.

Check both identities with:

```sh
sudo pf status
```

It reports:

```text
Deployed source: <SHA>
Workspace HEAD: <SHA or non-git>
Workspace differs from deployed: True/False
Workspace changes: ...
```

This distinction prevents a local edit from being mistaken for deployed code.

A manual update that finds a dirty/different workspace first includes that current
workspace in the pre-update checkpoint as `workspace.tar.gz`, then replaces `repo/` with
the selected GitHub revision. An unattended release update refuses a dirty/different
workspace instead of deleting local work automatically.

`deploy --current` also requires a clean Git checkout so the deployed identity remains
an exact commit.

## 9. Manual update

For active staging development:

```sh
sudo pf update --latest
```

Or select an exact revision/release:

```sh
sudo pf update --commit FULL_SHA
sudo pf update --release TAG
sudo pf update --release latest --channel prerelease
```

The update resolves a fixed SHA, checks CI, clones a separate candidate, builds candidate
images, checks the migration contract, asks for confirmation, stops application writes,
creates a verified pre-update checkpoint, rehearses approved migrations when necessary,
replaces the writable repository, then activates the new images.

Migration changes require explicit review:

```sh
sudo pf update --latest --allow-migrations
```

Existing historical migration files being modified or deleted remain refused. No lifecycle
command automatically performs `alembic downgrade`.

`--skip-ci` is a manual staging exception, not evidence that CI passed.

## 10. Backups and rollback

Create a verified revision checkpoint:

```sh
sudo pf backup
```

A v2.5 checkpoint contains:

```text
source.tar.gz          exact deployed source revision
workspace.tar.gz       only when the writable repo differs from deployed source
database.dump          active PostgreSQL database
database.list
manifest.json
manifest.sha256
```

The active database dump is restored into a temporary database as part of verification.
The runtime `.env` is no longer stored in the normal revision `source.tar.gz`; it is host
configuration outside the repository. A full purge-recovery bundle preserves it separately.

List checkpoints, newest first, ten per page:

```sh
sudo pf backups --page 1
```

Interactive rollback:

```sh
sudo pf rollback
```

Code-only rollback keeps current data and requires schema compatibility:

```sh
sudo pf rollback BACKUP_ID
```

Application + database rollback:

```sh
sudo pf rollback BACKUP_ID --restore-db
```

The database form requires a stronger confirmation, restores the old dump into a fresh
database, and retains the previously active database under a `pf_keep_*` name rather than
silently deleting newer writes.

## 11. Reset staging data

To keep the installed application/version but activate a clean migrated database:

```sh
sudo pf reset-db
```

Confirmation uses the actual database name:

```text
RESET partflow_staging
```

`reset-db` creates and verifies a checkpoint first, initializes a new database with the
current Alembic head, switches databases transactionally, and retains the previous active
database. It does not delete the PostgreSQL Docker volume.

Use `reset-db` for clearing test data while keeping the deployment. Use `purge` when the
goal is to return the instance to a genuinely new-deployment state.

## 12. Full purge, recovery, and clean redeploy

### List/select instances

```sh
sudo pf instances
```

If multiple managed PartFlow Compose projects exist, `purge` presents a paginated list
(10 per page) unless `--project` selects one explicitly:

```sh
sudo pf purge
sudo pf purge --project partflow-staging
```

### Purge safety sequence

Before deletion, the tool prints a summary of project, repo, source revision, database,
containers, volumes, networks, image tags, checkpoints, state, and environment presence.
It then requires multiple confirmations.

The first confirmation is:

```text
PURGE <project>
```

The controller stops application writes and creates a verified recovery bundle. Only after
that succeeds does it request the destructive confirmations, including:

```text
DELETE <database>
ERASE <project> <random-challenge>
```

Deleting normal revision backups or resetting `pf-config.json` adds separate confirmations.
There is no `--yes` bypass.

### Purge recovery bundle

Stored under:

```text
recovery/<project>/purge-<timestamp>-<sha12>-<suffix>/
```

It preserves as much functional state as can be safely reconstructed:

```text
source.tar.gz                 exact deployed source
workspace.tar.gz              current editable repo when it differs
configuration/.env            external runtime environment
configuration/pf-config.json  admin settings snapshot
images.tar                    current/available PartFlow application images
postgres-globals.sql
databases/active.dump
databases/<retained>.dump
revision-checkpoints.tar.gz
state/*.json
manifest.json
manifest.sha256
```

Database dumps are restore-tested. If the PostgreSQL data volume exists but a recoverable
backup cannot be produced, purge refuses to delete that volume.

The recovery bundle is intended to reconstruct **functional PartFlow state**, not Docker
container IDs/network IDs bit-for-bit.

### Backup retention during purge

Keep normal revision checkpoints:

```sh
sudo pf purge --keep-backups
```

Archive them into recovery then delete the normal checkpoint tree:

```sh
sudo pf purge --delete-backups
```

Reset the local admin config too:

```sh
sudo pf purge --reset-admin-config
```

The root-owned `control/` plane remains installed so a clean deployment can be started
immediately afterward.

### Interrupted purge

If power/SSH fails after destructive deletion begins, run `purge` again. The operation
journal identifies the incomplete purge and requires a resume confirmation before
continuing from the verified recovery bundle.

### Brand-new redeploy after purge

```sh
sudo pf deploy --latest
```

Because purge removes `config/.env`, a normal full purge causes the deploy wizard to create
a new environment and new PostgreSQL password. If the purge variant preserved an external
configuration for a specific recovery path, the deploy flow validates it before reuse.

After smoke testing:

```sh
sudo pf backup
```

### Exact instance restore

List recovery bundles:

```sh
sudo pf recoveries
sudo pf recoveries --page 2
sudo pf recoveries --project partflow-staging
```

Restore a purged instance into an empty target project:

```sh
sudo pf restore-instance RECOVERY_ID
```

The restore recreates the saved repository workspace, restores `config/.env`, loads saved
application images, recreates/restores the database set, restores checkpoint history/state,
and health-checks backend/frontend. The current installed root-owned control plane is kept;
recovery does not downgrade the lifecycle controller mid-operation. The current
`config/pf-config.json` also remains authoritative. Its saved recovery copy is retained for
manual comparison/reapplication rather than being activated in the middle of a restore.

### Side-by-side old-data recovery

If a new instance is already active but old data is needed for inspection/export:

```sh
sudo pf restore-instance RECOVERY_ID --side-by-side
```

The old active database is restored under an isolated `pf_recovery_*` database name. The
current application/database are not replaced.

PartFlow does **not** perform a generic automatic merge between the recovered DB and the
new active DB. `PartMovement`, quantity lineage, allocations, reversals, and derived current
state have domain invariants that cannot be safely reconciled by generic SQL insertion.
Recover old data side-by-side, then build an explicit domain-aware import/reconciliation
procedure for any data that truly must be carried forward.

## 13. Release checks and scheduled tasks

Check without applying:

```sh
sudo pf release-check
```

To allow unattended release updates, edit:

```text
/volume1/docker/partflow/config/pf-config.json
```

and set:

```text
"auto_update": true
```

Scheduled updates are stricter than manual updates: they require an eligible published
release, matching successful CI for the exact SHA, no migration/config/dependency condition
requiring human review, and a workspace that still exactly matches the deployed revision.

DSM Task Scheduler should call the root-owned wrappers, for example:

```sh
/volume1/docker/partflow/control/backup.sh
```

and:

```sh
/volume1/docker/partflow/control/release-check.sh --apply
```

Do not schedule `reset-db`, `purge`, `restore-instance`, or other interactive destructive
commands.

## 14. Advanced raw Compose access

Prefer `sudo pf ...`. The controller pins all important paths and serializes state-changing
operations.

If raw Compose access is absolutely necessary, the equivalent shape is:

```sh
sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo \
  docker compose \
  --project-directory /volume1/docker/partflow/repo \
  --env-file /volume1/docker/partflow/config/.env \
  -p partflow-staging \
  -f /volume1/docker/partflow/control/compose.nas.yaml \
  ps
```

Using raw Docker/Compose bypasses controller locks, recovery checks, and destructive guards.
Do not run it concurrently with `pf update`, `pf backup`, `pf reset-db`, `pf purge`, or
`pf restore-instance`.

Never use broad cleanup commands such as:

```text
docker system prune --volumes
docker volume prune
```

as a PartFlow reset mechanism on a NAS that may host other workloads.

## 15. Updating the control plane

Application `update` intentionally does not self-update `control/`.

When a reviewed repository revision contains a new Admin version:

```sh
cd /volume1/docker/partflow/repo
# Review the deployment/control changes and Git status first.
sudo sh ./deploy/synology/install-control.sh
sudo pf doctor
```

The installer archives the previous control directory under:

```text
recovery/control-upgrades/
```

This explicit step is the security boundary that permits `repo/` to remain users-writable.
Do not run an unreviewed or unknown `install-control.sh` with `sudo`.

## 16. Troubleshooting

### SMB can see `repo/` but cannot edit

First normalize POSIX permissions:

```sh
sudo pf permissions
```

Then verify DSM Shared Folder permissions grant the account/group Read/Write access.

### SMB cannot modify backups/recovery

That is intentional. These are recovery artifacts and remain group read-only. Copy them
elsewhere if an editable copy is needed.

### `sudo sh ./pf.sh ...` refuses to run

Expected in v2.5. The repo copy is source only. Run:

```sh
sudo pf ...
```

or install/update control first:

```sh
sudo sh ./deploy/synology/install-control.sh
```

### `.env` exists but Compose says variables are missing

Use `sudo pf doctor`. A raw Compose command does not automatically use the external config.
The authoritative runtime file is:

```text
/volume1/docker/partflow/config/.env
```

### Local source edits exist before an update

Check:

```sh
sudo pf status
```

A manual update preserves a differing workspace in `workspace.tar.gz` before replacement.
An unattended update refuses the drift and waits for manual review.

### Incomplete lifecycle operation

Run:

```sh
sudo pf status
```

Then use the operation-specific recovery (`resume`, `rollback`, repeat/resume `purge`, or
`restore-instance`) rather than deleting state files manually.

## 17. Command reference

| Command | Purpose |
| --- | --- |
| `sudo pf doctor` | Validate host tools, control security, Compose config, env, and basic capacity |
| `sudo pf permissions` | Normalize repo/config writable and backup/recovery read-only permissions |
| `sudo pf status` | Show deployed revision, workspace drift, DB revision, containers, pending operation |
| `sudo pf deploy --latest` | Brand-new staging deployment from latest configured branch SHA |
| `sudo pf deploy --commit FULL_SHA` | Brand-new deployment from an explicit commit |
| `sudo pf deploy --release TAG` | Brand-new deployment from a published release |
| `sudo pf abort-deploy` | Remove an incomplete first deploy before frontend access opened |
| `sudo pf update --latest` | Managed staging update from latest branch SHA |
| `sudo pf update --commit FULL_SHA` | Managed staging update to exact commit |
| `sudo pf update --release TAG` | Managed staging update to release |
| `sudo pf backup` | Create and restore-test a revision checkpoint |
| `sudo pf backups --page N` | List revision checkpoints, 10/page |
| `sudo pf rollback [BACKUP_ID]` | Code rollback with current DB retained |
| `sudo pf rollback BACKUP_ID --restore-db` | Restore code + selected database state |
| `sudo pf reset-db` | Activate a clean migrated DB while preserving recoverability |
| `sudo pf instances` | List managed PartFlow instances |
| `sudo pf purge [--project NAME]` | Full recoverable purge of one staging instance |
| `sudo pf recoveries` | List purge recovery bundles |
| `sudo pf restore-instance RECOVERY_ID` | Recreate a purged functional instance |
| `sudo pf restore-instance RECOVERY_ID --side-by-side` | Restore old DB alongside current instance |
| `sudo pf release-check` | Check eligible release without applying |
| `sudo pf release-check --apply` | Apply unattended update only when every gate passes |
| `sudo pf resume` | Resume only an unchanged early-failure state |

## 18. Validation boundary

The included offline tests simulate Docker/PostgreSQL behavior while exercising controller
logic, filesystem/archive/checksum handling, permission policy, source/workspace separation,
purge/recovery flow, and path construction. They do not replace a real DSM + Docker +
PostgreSQL integration rehearsal.

Before relying on v2.5 recovery on important data, perform at least one disposable staging
cycle on the actual NAS:

```text
install-control
→ doctor
→ backup
→ update
→ purge
→ restore-instance
→ verify UI/data
→ purge
→ deploy --latest
→ restore old DB --side-by-side
```

Do not call a staging procedure production-ready until the repository's production phase
and backup/disaster-recovery gates are completed separately.
