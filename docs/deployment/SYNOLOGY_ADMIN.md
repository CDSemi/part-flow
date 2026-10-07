# PartFlow NAS Admin v2.5

> **English is the source of truth.** [Vietnamese translation](./SYNOLOGY_ADMIN.vi.md).
>
> Version: **2.5.0**
> Prepared: **2026-09-11**
> Scope: restricted-LAN **Synology staging** administration. This is not a production-hardening package.

> **Deployment Admin checkpoint PF-A1.1 (2026-09-15, audit-r1 and audit-r2 corrections) — development state, not a NAS release.**
> The controller source in this repository now requires a *protected instance registration*
> (`pf_instance.py`: installation root with `bootstrap/` (launcher `pf`, `bootstrap.conf`,
> verifier `pf_bootstrap.py`), `registry/instances.json` and `registry/reservations/`,
> `locks/`, `releases/<id>/` with a pinned `control-manifest.json`, `instances/<uuid>/record.json`).
> The installed launcher runs the bootstrap verifier first: interpreter, configuration,
> every ancestor and the whole pinned release tree are checked before any release code is
> executed. Construction, `--help`, `instances`, `status`, `backups`, `recoveries` and the
> default `doctor` are read-only; `status`/`doctor` print identity, trust summary and the
> pending journal first and issue **no** Git/Docker/Compose command when the context or the
> runtime configuration is refused. Mutating commands need `--instance <slug|uuid>` (or a
> protected default), a clean managed-path inventory (no repeated, nested or aliased paths
> across instances), the stable per-instance lock under `<root>/locks/` and an explicit
> journal route. Managed and authoritative paths are accepted in exactly one canonical POSIX
> spelling (single leading `/`, no empty, `.` or `..` component, no trailing `/`); any other
> spelling such as `//volume1/...` is refused, never normalized. The installation root is
> chosen by the installed bootstrap alone: `--installation-root` is the verifier→release
> handshake, and an operator-supplied `--installation-root` anywhere on the command line
> refuses the whole invocation before any registry or journal is read. Two published records
> claiming the same daemon `engine_id` and Compose project are reported as a `CONFLICT` by
> `instances` and refuse mutation on both sides until an administrator repairs the registry.
> The v2.5 layout described below is **unregistered** in this checkpoint: the installed
> `control/pf.sh` prints a shell-only read-only report, executes no Python from the legacy
> control directory and names a pending journal without reading or printing its contents.
> `install-control.sh` remains the legacy v2.5 installer; do not run it against a live NAS
> with this checkpoint. Registration exists only as a Python transaction for disposable
> fixtures (`register_instance`), not as an operator command.

> **Deployment Admin checkpoint PF-A1.2 (2026-09-15) — runner, frozen configuration, protected
> source store; still a development state, not a NAS release.** Every child process of the
> control release (Git, Docker/Compose, the SQL tools inside the `db` container, host
> diagnostics) now goes through one runner (`pf_runner.py`): the executable must be
> registered by the trusted installer in `<root>/bootstrap/tools.conf` (`docker`,
> optional `docker_compose`, `git`, `ip`, `hostname`; absolute paths, validated through a
> trusted link chain and protected ancestors, never looked up on `PATH`), the child
> environment is built from an allowlist (fixed `PATH`, the instance's private
> `instances/<uuid>/home` as `HOME`, `DOCKER_CONFIG` inside it with an empty client
> configuration and plugin directory, `DOCKER_HOST` from the registered daemon endpoint,
> `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null`), arguments are arrays, captured
> output is bounded and redacted (the database password and its URL-encoded form never
> reach a message or log), every call has a deadline, and a timeout or interruption
> terminates the whole process group. Interruption means `SIGINT`, `SIGTERM`, `SIGHUP` (for
> example an SSH session that drops) or `SIGQUIT`: the controller terminates the child group,
> records the effect and runs its fail-closed stop before it releases the instance lock, and
> a repeated signal does not cut that short (only `SIGKILL` or power loss can; recovery from
> those is the next launch's job). Every mutating child (Compose `up`/`down`/`stop`/
> `build`/`run`, `createdb`/`dropdb`/`pg_restore`, mutating SQL, Docker `tag`/`rm`/`image load`,
> store `fetch`) carries an effect descriptor (kind, verb, targets — never application values),
> so its timeout or interruption is recorded under
> `instances/<uuid>/operations/<id>/unresolved-effects.json` and listed by `status`/`doctor`
> before any retry; read-only children (`ps`, `config`, `logs`, `inspect`, `pg_dump`,
> `pg_restore --list`, health probes, `SELECT`s) record nothing. PostgreSQL client programs
> run inside the `db` service as direct argument vectors (no `sh -c`), connecting as the
> frozen configuration's `POSTGRES_USER`.
> `config/.env` is parsed strictly (only the seven PartFlow keys, no duplicates, no
> `export`, no expansion, single-/double-quoted or unquoted literals; see section 6) and
> every mutating command first freezes it into a private 0400 snapshot
> `instances/<uuid>/operations/<id>/app.env`; the operation consumes that snapshot, an
> edit of `config/.env` during the operation is detected, and an existing value that cannot
> be frozen literally (a single quote, a trailing backslash, control characters) is an explicit
> `migration-issue` — it is never rewritten or regenerated. The backend connection URL is
> generated with percent-encoded credentials as `PARTFLOW_DATABASE_URL` (`compose.nas.yaml` no
> longer splices `POSTGRES_PASSWORD` into a URL). Privileged Git never runs against `repo/`:
> sources are fetched into a protected bare store under `<root>/sources/` (own configuration,
> no hooks/fsmonitor/includes/alternates, HTTPS only) and exported blob by blob (submodules,
> symbolic links, Git LFS pointers and tracked paths named like ignored workspace artifacts —
> `.env`, `node_modules`, `.venv`, `__pycache__`, `.pytest_cache` — are refused, so nothing
> deployed is ever outside the manifest); the workspace is compared byte/mode against a
> protected manifest recorded when the tool deployed a tree; a tree without such
> provenance is `unknown`, never assigned a commit SHA (section 8). Read-only diagnostics
> hand Compose a registration-created empty env-file and the values as allowlisted
> variables, so no editable file is read by Compose. Compose passthrough is bounded to known
> words and refuses the raw `config` dump until PF-A1.4 removes the route;
> `install-control.sh` remains the legacy installer.

> **Deployment Admin checkpoint PF-A1.3 (2026-10-06) — Compose envelope, daemon binding, exact
> resource inventory; still a development state, not a NAS release.**
> *Daemon binding.* Registration resolves the Docker socket path (system links such as
> `/var/run` → `/run`) and stores the resolved `unix://` endpoint; validation checks it offline
> (no symbolic-link component, protected ancestors, a root-owned socket that is not
> world-writable). A missing socket is reported as a note (a stopped daemon), not as a trust
> failure. Before the first Docker/Compose child of every process the controller runs
> `docker info` once and requires the registered engine ID and a rootful daemon; drift
> (`daemon-drift`), rootless mode (`daemon-rootless`), an unusable answer (`daemon-info-invalid`)
> or no answer (`daemon-unreachable`) refuses every mutation before any confirmation (the
> `RESUME PURGE`/`RESUME ABORT DEPLOY` prompts of an interrupted purge or abort included), journal
> write, pause or Docker effect. `status`/`doctor` print the daemon line, mark every later
> Docker section `unavailable: <code>` without contacting the daemon again, and still print
> every non-Docker section.
> *Compose envelope.* Before any `up`, `run`, `build`, `create`, `start`, `restart`, `scale` or
> `watch` (managed or passthrough) the controller renders `docker compose … config --format json` with exactly the
> inputs of that call (installed `compose.nas.yaml`, protected image override, frozen env-file
> and the effective child values, per-call temporary database names included) into a private
> 0600 `compose-<n>.json`, validates it against the PartFlow topology allowlist (three
> services, `postgres_data` and `default` only, no host-privilege options, no bind mounts or
> Docker socket, one frontend port on the approved address, the instance label everywhere)
> and compares every application value literally, and records the input hashes in
> `compose-envelope.json`. Only `POSTGRES_DB=pf_migrate_*`/`pf_clean_*` (the update rehearsal and
> reset-db databases) may be overridden per call. Compose v1 is unsupported (it cannot render
> `config --format json`).
> *Instance label.* `compose.nas.yaml` stamps `io.deploy-admin.instance-id` on the three services,
> both image builds, the `postgres_data` volume and the `default` network, from
> `DEPLOY_ADMIN_INSTANCE_ID`, which the controller generates from the protected registration.
> *Exact inventory.* All containers (stopped ones included, `Config.Env` never read), the topology
> volume/network names and the labelled volumes, networks and image tags are classified as
> owned, excluded (references, bind paths, owned resources outside the topology, ungrammatical
> tags, and foreign-in-use tags: a container of another application uses the tag or, when it
> was created from an image ID, that image ID) or blockers (`resource-legacy-unlabeled`, `resource-name-collision`,
> `resource-foreign-claim`, `resource-label-conflict`, `resource-shared`,
> `resource-unsupported-driver`). Name prefixes never select anything. `deploy` and exact
> `restore-instance` require an empty target (`resource-target-not-empty`); `backup`, `update`,
> `rollback`, `reset-db`, `resume`, side-by-side restore, `release-check --apply` and mutating
> passthrough run an ownership preflight right after the lock (`resource-not-owned`). Legacy or
> foreign resources are never adopted automatically (adoption is PF-A2). A container that
> disappears between `docker ps -a` and its inspect (another application's short-lived
> container) makes the inventory list again; after three such attempts the step stops with
> `inventory-unstable`.
> *Closed deletion plan.* `purge` and `abort-deploy` delete exactly a frozen plan
> (`deletion-plan.json`, hashed in the journal): every item and every container that uses a
> volume or network is reinspected before its effect and a present item must still be owned (for
> example an image tag a foreign container started to use), a change stops with `plan-drift`, resume
> runs the same plan and never adds a resource, and an image tag is deleted only when the
> purge recovery bundle covers its image ID. Nothing prunes, nothing uses `compose down -v`,
> image removal is never forced, and bind-mounted paths are never deleted. `abort-deploy` no
> longer runs `compose down --volumes`; it removes the planned containers, network and volume
> and keeps the images.

> **Deployment Admin checkpoint PF-A1.4 (2026-10-06) — every entry route on the A1 primitives, no
> catch-all Compose; PF-A1 closed offline, still a development state, not a NAS release.**
> *No catch-all Compose.* `pf` accepts exactly the managed commands of section 17 and two
> read-only Compose views. Every word the former Compose passthrough forwarded (`up`, `start`,
> `restart`, `create`, `scale`, `watch`, `unpause`, `down`, `stop`, `kill`, `pause`, `rm`, `run`,
> `exec`, `cp`, `attach`, `build`, `pull`, `push`, `version`, `top`, `images`, `port`, `ls`,
> `events`, `stats`, `wait`, `config`) is refused with `compose-route-removed` and names the managed
> alternative; a leading Compose or Docker global option (`-f`, `-p`, `--env-file`,
> `--project-directory`, `--profile`, `-H`, `--context`, …) is refused with
> `compose-override-refused`; any other leading option, an abbreviation such as `--inst` included,
> with `unknown-option`; an unknown word with `unknown-command` (all exit 1). These refusals happen
> before the registry is read, a lock is taken or any process starts. Abbreviated options are
> refused by every command.
> *Read-only views.* `pf ps` and `pf logs` (section 14) rebuild the Compose command from their
> parsed options only (services `db`, `backend`, `frontend`) and run it through the validated
> context, the runner and the daemon binding with redacted, bounded output; they take no lock and
> create no operation. `pf` without a command is `pf ps`.
> *Commands without a terminal.* A command started without a terminal (a scheduled task, a script,
> `ssh` without `-t`) is refused before the lock: a command that asks for a typed confirmation with
> `terminal-required`; `backup`, `permissions` and `release-check` must name the instance
> (`--instance <slug|uuid>`, else `instance-required-unattended`) and then need a protected policy
> grant for their class of operation, which no policy grants in this checkpoint
> (`policy-grant-required`, exit 20; grants arrive with PF-A4.3). `release-check --apply` is refused
> with `auto-apply-not-permitted` (exit 20) with or without a terminal; `auto_update` in
> `pf-config.json` is a proposal only. The scheduler wrappers `backup.sh` and `release-check.sh`
> require `--instance` and execute only their sibling launcher (section 13).
> *Fail-closed stop.* After a failed operation with a pending journal the controller stops only
> this instance's own Compose one-off jobs from the exact inventory (one that already vanished is
> reported and skipped), then the application services; the legacy `partflow.admin.project` label
> selects nothing. The message names `pf --instance <slug> status`.
> *Restore authority.* `recoveries` and `restore-instance` list and verify bundles only in the
> selected instance's own `recovery/<project>/` directory. `--project` is a Compose project name,
> never a path, and with `--instance` it must name that instance's project (`selection-conflict`).
> A bundle outside that directory, or a link to one, is never listed and is refused
> (`recovery-outside-instance`). A bundle restores only `deployed.json`, `last-reset.json` and
> `observed-tags.json` into protected state, and its manifest must name them as a list
> (`recovery-state-file-refused`).
> This block **supersedes** the passthrough sentences of the PF-A1.2 and PF-A1.3 blocks above.

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

and, since PF-A1.3, the instance identity that labels every resource Compose creates
(`DEPLOY_ADMIN_INSTANCE_ID`, generated from the protected registration, never read from
`.env`).

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
the external `.env` or the installed Compose file. See §14 for the explicit advanced form,
which must also name `DEPLOY_ADMIN_INSTANCE_ID`.

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

> **Warning (PF-A1.4).** `install-control.sh` installs the legacy v2.5 layout, whose launcher is
> read-only in this checkpoint (section 15). On a live v2.5 NAS it replaces the working v2.5
> `control/` directory (archived under `recovery/control-upgrades/`) with a read-only one until the
> PF-A2 installer exists. Do not run it on a NAS you still administer with v2.5.

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
8. Runs `pf permissions` to normalize repository/config/backup/recovery modes. In this
   checkpoint that call reaches the legacy read-only launcher, which refuses `permissions` on an
   unregistered installation; under `set -eu` the installer stops there and its "installation
   complete" message is not printed. The control directory stays the legacy read-only one until
   PF-A2.

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

`auto_update` is a proposal only: unattended apply needs a protected policy grant, which this
checkpoint does not provide (section 13).

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

Since PF-A1.2 the file is parsed as data with a strict grammar: exactly these seven keys,
each once, `KEY=VALUE` with no whitespace around `=` and no `export`; comment lines start
with `#`; a value is unquoted (no whitespace, quotes or `#`; `$` and `\` are literal),
single-quoted (`'...'`, literal, cannot contain `'`) or double-quoted (only `\\` and `\"`
escapes, no `$` expansion). Values are never expanded, evaluated or rewritten. A value that
cannot be rendered literally into the private snapshot (a single quote, a trailing backslash,
control characters) is reported as `migration-issue` and blocks mutating commands until the
file is fixed by hand; the existing password is never regenerated.

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

Since PF-A1.3 an existing unlabeled or foreign Compose topology resource (container, volume or
network) refuses `deploy` with `resource-target-not-empty` before any change, and
`abort-deploy` prints and freezes the exact plan before `ABORT DEPLOY <project>`; it never
runs `compose down --volumes`, keeps the images, and an interrupted abort resumes the same
plan after `RESUME ABORT DEPLOY <project>`.

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
Workspace: provenance git_commit|unknown | manifest commit <SHA or none> | differs from deployed: True/False | changes: ...
```

This distinction prevents a local edit from being mistaken for deployed code. Since
PF-A1.2 the workspace line comes from an fd-safe byte/mode comparison of `repo/` against
the protected manifest the tool recorded when it deployed a tree
(`instances/<uuid>/artifacts/source-manifest.json`); no Git command runs against `repo/`,
its `.git` metadata (hooks, fsmonitor, filters, includes, alternates, remotes) is editor
data and is never consulted. A workspace without such a manifest, or one that differs from
it, has `unknown` provenance: `status` still works, but no commit SHA is invented.

The comparison ignores *untracked* workspace artifacts by name only (`.env`,
`node_modules`, `.venv`, `__pycache__`, `.pytest_cache`; `.git` is control metadata). That
policy never hides tracked content: a commit that tracks a path with one of those names
is refused by the source store before export (`unsupported source path (tracked reserved
workspace artifact name)`), a candidate tree carrying one is refused before `repo/` is
touched (in `rollback` and `restore-instance`, together with the store's provenance proof,
before any confirmation, pending journal, `config/.env` change, pause, safety snapshot or
database swap), and a manifest that lists one cannot be verified — the tool fails closed instead
of reporting a match it did not prove.

A manual update that finds a dirty/different workspace first includes that current
workspace in the pre-update checkpoint as `workspace.tar.gz`, then replaces `repo/` with
the selected revision exported from the protected source store. An unattended release
update refuses a dirty/different workspace instead of deleting local work automatically.

`deploy --current` proves the workspace instead of trusting it: the commit the checkout
claims (`.git/HEAD`, read as data) is fetched into the protected store, exported to a private
candidate, and the workspace must equal that tree byte for byte; otherwise the command stops
with the differences and asks for an explicit `--commit`/`--latest`/`--release`. A tree
copied in from a ZIP or an unverified checkout is therefore never deployed as "current".

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

Since PF-A1.3 an existing unlabeled or foreign Compose topology resource refuses `update`
with `resource-not-owned` right after the lock, before any confirmation or change.

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

Since PF-A1.3 the sequence is:

1. **Preliminary plan.** The controller inventories the bound daemon exactly (see the PF-A1.3
   checkpoint note at the top) and builds an advisory deletion plan. Any blocker (a legacy, name-collided,
   foreign-claimed, label-conflicting, shared or unsupported resource) refuses the purge with
   `resource-blocked` here: no confirmation, no pause, no bundle.
2. **Summary.** It prints project, repo, source revision, database, the exact containers,
   volumes and networks, the image tags pending recovery-bundle coverage, every retained
   exclusion and every retained bind path, checkpoints, state and environment presence.
3. **First confirmation** `PURGE <project>`, then application writes stop.
4. **Bundle and binding plan.** The verified recovery bundle is created. After `images.tar` is
   verified the controller inventories again, builds the binding plan (image tags covered by
   an image ID saved in the bundle become candidates; other owned tags are retained and
   reported) and compares it with the preliminary plan, ignoring only the tags this purge
   created itself. A difference stops with `plan-changed` and reopens the application; the
   bundle folder then has no `manifest.json`. A resource that became a blocker in this window
   is also `plan-changed` (never the pre-confirmation `resource-blocked` copy): a foreign user
   of the volume or network (`resource-shared`) reopens the application, any other blocker
   keeps the services stopped with the journal `paused`, because Compose could adopt or
   recreate it; resolve it, then run `pf resume`. Otherwise `resources_before_purge` is sealed
   into the manifest from the binding plan.
5. **Destructive confirmations**, after the full binding plan is printed:

   ```text
   DELETE <database>
   ERASE <project> <random-challenge>
   ```

   Deleting normal revision backups or resetting `pf-config.json` adds separate
   confirmations. There is no `--yes` bypass.
6. **Closed execution.** The plan is written durably (`deletion-plan.json`, its hash in the
   journal) before the first deletion. Containers, then the network, the volume and the
   covered image tags are removed one by one; each item, and every container that uses the
   volume or network (stopped ones included), is reinspected immediately before its effect,
   and a change (including a present item that is no longer owned) stops with `plan-drift`.
   Nothing is pruned, `compose down -v` is never used,
   image removal is never forced, and bind-mounted paths are never deleted.

The guarantee assumes a quiescent, trusted daemon: Docker has no atomic compare-and-delete,
so a concurrent Docker administrator is outside it.

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
journal identifies the incomplete purge and its frozen plan; after
`RESUME PURGE <project> <recovery-id>` only the remaining planned items are processed (already
removed ones are recorded as `already-absent`), a newly appeared resource is never added, and
a missing, moved, symlinked or tampered plan is refused with `plan-invalid`. A journal written
before PF-A1.3 (no frozen plan) is refused with `plan-missing`: review the remaining resources
manually.

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

Since PF-A1.3 the empty-target test is the exact inventory: any owned or blocking container,
volume or network of the project refuses with `resource-target-not-empty` before any
confirmation.

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

Check without applying, interactively:

```sh
sudo pf --instance <slug> release-check
```

Apply a selected release manually with `sudo pf --instance <slug> update --release <tag>`.

**Scheduled tasks are not available on a pf-managed instance in this checkpoint.** Without a
terminal, `backup` and `release-check` are refused with `policy-grant-required` (exit 20) and
`release-check --apply` with `auto-apply-not-permitted` (exit 20): unattended operation needs a
protected policy grant for that class of operation, and the grants arrive with PF-A4.3. The
editable `"auto_update": true` in `config/pf-config.json` does not enable anything; it is a
proposal only. Until then run the checkpoint interactively and copy it off-NAS:

```sh
sudo pf --instance <slug> backup
```

Once grants exist, a root-owned DSM task names its instance and runs the wrapper the PF-A2
installer places next to the installed launcher, or the launcher itself:

```sh
<root>/bootstrap/backup.sh --instance <slug>
<root>/bootstrap/release-check.sh --instance <slug> [--channel stable|prerelease] [--apply]
<root>/bootstrap/pf --instance <slug> backup
```

The wrappers accept exactly these arguments (anything else prints usage and exits 2), run with
a fixed `PATH` and execute only their sibling launcher. Copies under a legacy `control/`
directory reach the read-only legacy launcher, which refuses `backup` and `release-check` on an
unregistered installation.

Scheduled updates, once granted, stay stricter than manual updates: they require an eligible
published release, matching successful CI for the exact SHA, no migration/config/dependency
condition requiring human review, and a workspace that still exactly matches the deployed
revision.

Do not schedule `reset-db`, `purge`, `restore-instance`, or other interactive destructive
commands; without a terminal they are refused anyway (`terminal-required`).

## 14. Advanced raw Compose access

Prefer `sudo pf ...`. The controller pins all important paths and serializes state-changing
operations.

Since PF-A1.4 `pf` no longer forwards Compose commands. Two read-only views remain:

```sh
sudo pf --instance <slug> ps [-a] [-q] [--services] [--status STATUS] [--format table|json] [SERVICE...]
sudo pf --instance <slug> logs [--tail N] [-f] [-t] [--no-color] [--no-log-prefix] [--since V] [--until V] [SERVICE...]
```

`SERVICE` is `db`, `backend` or `frontend`; `STATUS` is one of `paused`, `restarting`,
`removing`, `running`, `dead`, `created`, `exited`; `V` is a duration such as `30m` or `2h`, or
an RFC 3339 date/time. `--tail` defaults to 200 and allows 1–10000. `logs -f` ends after 1 hour
or 64 MiB of output with `logs-bound-reached` (exit 1); the output shown until then is complete.
Both views run through the validated context, the registered tools and the daemon binding, redact
the database password, take no lock and create no operation. Any other Compose word or option is
refused (section 16).

There is no managed route for an application CLI inside the backend container (`exec` and `run`
are refused; a managed route is PF-A4).

If raw Compose access outside the controller is absolutely necessary, the equivalent shape is:

```sh
sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo \
  PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<db>' \
  DEPLOY_ADMIN_INSTANCE_ID=<instance UUID from 'pf instances'> \
  docker compose \
  --project-directory /volume1/docker/partflow/repo \
  --env-file /volume1/docker/partflow/config/.env \
  -p partflow-staging \
  -f /volume1/docker/partflow/control/compose.nas.yaml \
  ps
```

Since PF-A1.2 `compose.nas.yaml` takes the backend connection URL from
`PARTFLOW_DATABASE_URL`, which the controller generates with percent-encoded credentials;
a raw invocation must supply it explicitly (the controller itself never passes `.env` to
Compose as an editable file: it hands over a frozen snapshot or a registration-created empty
env-file plus the allowlisted variables).

Since PF-A1.3 `compose.nas.yaml` also requires `DEPLOY_ADMIN_INSTANCE_ID`. **A wrong UUID
mislabels every resource Compose then creates**: those resources are classified
`resource-foreign-claim` or `resource-label-conflict` and block `purge`, `abort-deploy` and every
guarded command until they are reviewed.

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

> **Warning (PF-A1.4).** In this checkpoint `install-control.sh` installs the legacy layout, whose
> launcher is read-only: it refuses every command except the diagnostics report, including the
> installer's own final `pf permissions`. Running it on a live v2.5 NAS replaces the working v2.5
> control directory (archived under `recovery/control-upgrades/`) with a read-only one until the
> PF-A2 installer replaces it.

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

In this checkpoint that installer installs the legacy read-only layout (see the warning in
section 15); do not run it on a live v2.5 NAS.

### `compose-route-removed`, `compose-override-refused`, `unknown-option` or `unknown-command`

`pf` no longer forwards Compose commands (PF-A1.4). `compose-route-removed` names the managed
command that replaces the word (for example `pf resume`, `pf update` or `pf rollback` instead of
`up`; `pf purge` or `pf abort-deploy` instead of `down`); `compose-override-refused` means a
Compose or Docker global option such as `-f`, `-p` or `--env-file` (the project, files, env-file,
directory and daemon are fixed); `unknown-option` means anything else before the command,
including an abbreviation of `--instance`; `unknown-command` means an unknown word. Nothing was
read or changed. Use `pf ps` and `pf logs` for read-only Compose views (section 14).

### `terminal-required`

The command asks for a typed confirmation and was started without a terminal (a scheduled task,
a script, `ssh` without `-t`). Run it interactively: `sudo pf --instance <slug> <command>`.
Nothing was changed.

### `instance-required-unattended`

`backup`, `permissions` or `release-check` ran without a terminal and the instance was selected
by the protected default or as the only registration. An unattended command must name its
instance: `pf --instance <slug|uuid> <command>`. Nothing was changed.

### `policy-grant-required` or `auto-apply-not-permitted` (exit 20)

An unattended `backup`, `permissions` or `release-check`, or any `release-check --apply`, needs a
protected policy that permits that class of operation. No policy grants one in this checkpoint
(grants arrive with PF-A4.3); `auto_update` in `pf-config.json` does not. Run the command
interactively, and apply a release manually with `pf --instance <slug> update --release <tag>`.
Nothing was changed.

### `selection-conflict`, `recovery-outside-instance` or `recovery-state-file-refused`

`selection-conflict`: `--project` names a different project than the instance selected with
`--instance`; use `--instance` alone. `recovery-outside-instance`: the bundle is not a directory
of the selected instance's own `recovery/<project>/`; bundles of other instances, copies
elsewhere and links to them are never listed or restored. `recovery-state-file-refused`: the
bundle manifest lists a state file other than `deployed.json`, `last-reset.json` or
`observed-tags.json`, or its state files are not a list. Nothing was changed.

### `logs-bound-reached`

`pf logs` stopped at its bound (1 hour for `-f`, otherwise the diagnostic deadline, or 64 MiB of
output). The output shown is complete up to that point; narrow it with `--since`, `--tail` or a
service name.

### `.env` exists but Compose says variables are missing

Use `sudo pf doctor`. A raw Compose command does not automatically use the external config.
The authoritative runtime file is:

```text
/volume1/docker/partflow/config/.env
```

If `doctor` reports `migration-issue` or a parse error for that file, the controller refused
the proposal (unknown/duplicate key, unsupported quoting, a secret that cannot be frozen
literally); fix the file by hand — the controller never rewrites it. A raw Compose command
additionally needs `PARTFLOW_DATABASE_URL` and `DEPLOY_ADMIN_INSTANCE_ID` (section 14);
`DEPLOY_ADMIN_INSTANCE_ID` is never accepted in `config/.env`.

### Docker daemon drift, unreachable or rootless refusal

`daemon-drift`: the registered endpoint answers as a different engine ID than the one the
instance is bound to. Every Docker/Compose step is refused; nothing was changed unless the
message names an operation and phase. Re-binding a daemon is an explicit installation
transaction (PF-A2); do not edit the record.

`daemon-unreachable`: the socket is absent (daemon stopped) or the daemon did not answer.
Start Docker (Container Manager) and rerun; `status` still prints every non-Docker section.

`daemon-rootless` / `daemon-info-invalid`: only a local rootful daemon with a usable identity is
supported.

`daemon-endpoint-*` in the trust summary: the registered socket path is not a protected
socket (symbolic link, untrusted owner, world-writable, replaceable ancestor). Nothing was
contacted.

### Compose envelope refused

`envelope-*` lists each finding with its JSON path (values are never printed): a hostile or
unexpected option in the resolved model, a changed protected image override
(`envelope-override`), or a render that failed (`envelope-render-failed`: Compose exit, more
than 4 MiB, invalid JSON or a duplicate key; Compose v1 cannot render `config --format json`
and is unsupported). Nothing was built, created or started.

### Resources blocked from purge or not owned

`resource-blocked` (purge, abort-deploy), `resource-not-owned` (guarded commands) and
`resource-target-not-empty` (deploy, exact restore) list each resource with its class. Review
them with `sudo pf status`; legacy unlabeled resources (for example from a v2.5 installation),
name collisions and resources claimed by another instance are never adopted or deleted
automatically (adoption is PF-A2). `plan-drift` during a purge or abort means a planned
resource or one of its users changed after the plan was frozen (or a present planned item is
no longer owned); the journal keeps the plan. `inventory-unstable` means containers kept
disappearing between `docker ps -a` and their inspect on three attempts; retry when the host is
quieter (an interrupted purge or abort resumes its frozen plan).

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
| `sudo pf abort-deploy` | Remove an incomplete first deploy before frontend access opened (confirm `ABORT DEPLOY <project>`; an interrupted abort resumes with `RESUME ABORT DEPLOY <project>`) |
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
| `sudo pf release-check --apply` | Refused in this checkpoint (exit 20): needs a protected policy grant (PF-A4.3) |
| `sudo pf resume` | Resume only an unchanged early-failure state |
| `sudo pf ps [options] [SERVICE...]` | Read-only Compose container view of the instance (section 14) |
| `sudo pf logs [options] [SERVICE...]` | Bounded, redacted service logs (section 14) |

Commands started without a terminal must pass `--instance <slug|uuid>`; until PF-A4.3 policy
grants exist every locked command is refused without a terminal (`terminal-required`,
`policy-grant-required`).

## 18. Validation boundary

The included offline tests simulate Docker/PostgreSQL behavior while exercising controller
logic, filesystem/archive/checksum handling, permission policy, source/workspace separation,
purge/recovery flow, and path construction. They do not replace a real DSM + Docker +
PostgreSQL integration rehearsal.

PF-A1.3 limits (offline evidence only; the Docker-daemon and NAS host gates are not run):

- a concurrent Docker or root administrator is outside the guarantee (no atomic
  compare-and-delete; items and users are reinspected under a quiescent trusted daemon);
- a volume recreated within the same second with identical metadata is indistinguishable;
- the NAS Compose version is unverified; the envelope rules were calibrated on one real
  Compose v2 render (Docker Desktop CLI);
- the rendered JSON is validated, not reused as the executed `-f` input;
- the Compose container-marker labels used for ownership are not calibrated on a real daemon;
- image coverage by image ID assumes a quiescent daemon between `image save` and the binding
  inventory.

PF-A1.4 adds these limits:

- unattended detection is terminal-based (stdin absent, closed or not a TTY); `ssh` without `-t`
  counts as unattended;
- no unattended operation, scheduled backup or release check included, runs on a pf-managed
  instance until a PF-A4.3 protected policy grant exists;
- the scheduler wrappers are not installed by this checkpoint (the PF-A2 installer places them);
- `install-control.sh` is still the legacy installer (sections 5 and 15);
- the Compose `run` one-off labels that `fail_closed` relies on are proven offline only;
- there is no managed route for an application CLI in the backend container.

**PF-A1 closure (offline).** With PF-A1.4 every entry route uses the A1 primitives (explicit
instance, one runner, daemon binding, Compose envelope, exact inventory) and no catch-all Compose
route remains; the PF-A1 safety scope is proven offline only. A1-T11…T14 stay blocked on a real
Docker daemon (owners PF-A3.4/PF-A5.1, including the real Compose `run` one-off labels) and A1-T17
on DSM ACL/SMB evidence (owners PF-A2.3/PF-A5.1). No finding is closed overall and nothing here
is production-ready.

Before relying on v2.5 recovery on important data, perform at least one disposable staging
cycle on the actual NAS. The cycle below starts with `install-control` and therefore **requires the
PF-A2 installer**; until it exists, start from a protected layout and run
`<root>/bootstrap/pf --instance <slug> doctor → backup → update → purge → restore-instance →
verify UI/data` interactively.

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
