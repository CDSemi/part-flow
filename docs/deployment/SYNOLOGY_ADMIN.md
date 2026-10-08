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
> `terminal-required`; `backup` and `release-check` must name the instance
> (`--instance <slug|uuid>`, else `instance-required-unattended`) and then need a protected policy
> grant for their class of operation, which no policy grants in this checkpoint
> (`policy-grant-required`, exit 20; grants arrive with PF-A4.3). PF-A2.3: `permissions check` and
> `permissions plan` are read-only and run without a terminal and without a grant; `permissions apply` is
> terminal-only (`terminal-required`). `release-check --apply` is refused
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

> **Deployment Admin checkpoint PF-A2.1 (2026-10-07) — installer and control generations; still a
> development state, not a NAS release.**
> *New installation root.* `sudo sh ./deploy/synology/install-control.sh init --root <root>` is the only
> repository installer verb: it reads the reviewed repository files as data, shows every conflict before
> anything moves, asks for `INSTALL CONTROL <release-id>`, builds the root in a locked private sibling
> directory, smoke-checks the new release with the registered interpreter and publishes the root with one
> rename. It writes the staging policy and the `partflow-staging-legacy` profile, places the scheduler
> wrappers in `<root>/bootstrap/`, and creates the global launcher (default `/usr/local/bin/pf`) only when
> that path is absent; an existing launcher (a v2.5 one, another root's or anything else) is never replaced.
> *Installed verbs.* From the installed control: `pf install status | register | migrate-legacy | control |
> resume`. Every verb but `status` needs a terminal; inputs that are not given are asked one by one, a
> read-only preflight reports **every** conflict at once (exit 1, nothing changed), and the frozen plan is
> confirmed with a typed phrase (`REGISTER <slug>`, `MIGRATE <slug>`, `INSTALL CONTROL <id>`,
> `SELECT CONTROL <id>`, `RESUME <op>`, `ABANDON <op>`).
> *Journal and resume.* Each operation writes `plan.json` and `journal.json` under
> `<root>/install-operations/<id>/` before its first effect and journals every effect before and after it.
> An interrupted operation stays open; `pf install resume` observes every effect on disk before it
> decides, and `pf install resume --abandon` restores the previous state where that is legal. An effect
> found in a state the installer did not write stops in `needs_operator`, which names the target and
> the next step; it is never a dead end. While an install operation is open, the routes it affects are
> refused with `install-operation-pending`.
> *Control generations.* `pf install control --source <reviewed tree>` stages a content-addressed release
> (`releases/r-<16 hex>`), smoke-checks it (including the live configuration of every instance),
> re-verifies it, then switches `bootstrap.conf` and every instance record as one binding set under the
> registry and instance locks, verifies end to end through the real bootstrap and restores the previous
> binding automatically if that fails. Exactly one generation is bound; old releases are retained and can
> be selected again with `--release`. No application restart, build, pull or migration happens; a changed
> `compose.nas.yaml` is reported as a separate application operation.
> *Defaults and v2.5.* No install operation changes the registry default. `migrate-legacy` copies legacy-only
> `.env`/`pf-config.json` into `config/` byte for byte (the legacy copies stay), registers the instance with
> the user's paths and leaves the v2.5 `control/` and its launcher as the working control plane: pf mutating
> commands on that instance stay refused (`legacy-control-active`) until legacy adoption is installed
> (OD-A21-05); `status`, `doctor`, `ps` and `logs` work.
> This block **supersedes** the `install-control.sh` warnings of the PF-A1.4 block and of sections 5 and 15.

> **Deployment Admin checkpoint PF-A2.2 (2026-10-07) — config wizards and admin-config schema migration; still a
> development state, not a NAS release.**
> *Versioned admin configuration.* `pf-config.json` has two readable forms: legacy schema 1 (no
> `schema_version`; omitted keys take frozen implicit values) and schema 2 (`"schema_version": 2` and every key
> explicit). Loading never migrates: `status`, `doctor`, every lifecycle command, the installer preflight and the
> control-install smoke read both forms as they are. Duplicate, unknown, missing (schema 2), mistyped and
> unsupported-version files are refused with the first problem.
> *`pf config admin`.* Creates `pf-config.json` from the installed example, migrates a schema 1 file to schema 2
> (explicit values kept, implicit values written as the frozen schema 1 defaults) or completes a schema 2 file.
> It asks only the groups that are missing or uncertain, from a read-only list of the host's groups (groups are
> never created), shows a summary, asks `Write <path>? [y/N]` and writes atomically without overwriting an
> edit made in the meantime. Before registration, `pf config admin --configuration <dir> --project <project>`
> creates the file `pf install register` needs.
> *`pf config app`.* Creates or completes `.env` for the instance's profile: existing secrets are kept byte for
> byte; a database password is generated only when it is absent and the instance was never deployed; database
> credentials are never asked or rewritten after the first deployment; a new timezone must exist in the host's
> installed zone data. The first `pf deploy` without `.env` runs the same wizard.
> *Audit.* A `pf config admin` or `pf config app` write on a registered instance records `config-change.json`
> in its operation directory (key names and `unchanged`/`set` for secrets; no secret value and no `.env` hash).
> The wizard run inside the first `pf deploy` writes no `config-change.json`: that deploy operation's
> `operation.json` and its frozen `app.env` snapshot are the evidence. The pre-registration mode writes no record
> (no instance exists yet).
> *Audit fixes (uncommitted over `5d102d1`).* The pre-registration wizard refuses while any registered instance
> record cannot be loaded (`registry-record-invalid`), and its `admin-config-invalid` copy repeats
> `--configuration`/`--project`; choosing the DSM Reverse Proxy in `config app` re-asks a kept `localhost`
> hostname; a present `SITE_TIMEZONE` that is not a zone name (a `.`/`..` component) is asked instead of kept;
> `.env` parser errors never show a character of the line; an interrupt after the write is reported as
> `config-interrupted` or `config-audit-not-recorded` (the file already holds the new content); the
> `install-control.sh init` completion names `config admin --configuration`.
> This block **supersedes** the "create the configuration by hand" steps of the PF-A2.1 block and of section 5.

> **Warning (PF-A2.3).** Deployment Admin checkpoint PF-A2.3 (2026-10-07) — semantic permission policy,
> `check`/`plan`/`apply`, scope floors and a resumable apply; still a **development state, not a NAS release**.
> Offline evidence only: nothing was run against a real DSM host, SMB client or Docker daemon.
> *Commands.* `pf permissions check` (compare, read-only, exit 1 on any difference), `pf permissions plan`
> (preview with group members, counts and a plan hash, read-only) and `pf permissions apply` (numbered wizard,
> one typed confirmation `APPLY PERMISSIONS <slug>`, then a fenced, journaled and verified apply; terminal
> only). Bare `pf permissions` no longer changes anything: it is refused with `permissions-verb-required`
> (exit 2). `check` and `plan` take no lock and write nothing anywhere.
> *Approval.* A confirmed `apply` writes the **permission policy revision** N to
> `<root>/instances/<uuid>/permission-policy.json` (root, `0600`, kept by `purge`). Until the first approval
> the instance uses a derived policy: backups and recovery bundles keep the group of their folders, and the
> workspace and configuration use `workspace_write_group`. `backup_read_group` is a proposal for every
> instance, `workspace_write_group` once a revision exists; editing `pf-config.json` alone no longer changes
> who can read backups or recovery bundles.
> *Flows.* No lifecycle command walks an editable tree any more: `deploy --current` leaves workspace
> permissions unchanged, and backups, recovery bundles, replaced or restored source, a restored `.env` and
> restored state files are content-only copies that get explicit policy targets and are verified.
> This block **supersedes** the `sudo pf permissions` steps of sections 2 and 16 and the `backup_read_group`
> note of the PF-A2.2 block and section 6.

> **Warning (PF-A3.1).** Deployment Admin checkpoint PF-A3.1 (2026-10-07) — immutable deployed-source artifacts,
> strict lifecycle wire schemas and emergency preservation; still a **development state, not a NAS release**.
> Offline and filesystem evidence only: Docker and PostgreSQL were simulated, nothing was run against a real DSM
> host, Docker daemon or PostgreSQL server.
> *Deployment record.* Every successful `deploy`, `update`, `rollback` and `restore-instance` now seals a
> **deployment record** in `<root>/instances/<uuid>/artifacts/deployments/<deployment-id>/` (root only, `0700`/`0600`):
> the exact deployed source archive and its manifest, the resolved Compose model, the frozen `.env` (secrets) and
> the record itself. `deployed.json` points to it. It survives a checkout change, the loss of `repo/.git` and
> `purge`.
> *Bundles.* New checkpoints and purge recovery bundles are written as `schema_version` 1 manifests
> (`contracts/lifecycle-records.schema.json`) and read strictly: the exact manifest bytes, every payload's size and
> hash, no link and no unlisted file, before anything is extracted, confirmed or journaled. Older format 1 and 2
> bundles stay usable through an explicit, deterministic in-memory migration; nothing on disk is rewritten. A file
> such a bundle does not list is recorded and never opened: `restore-instance` takes the runtime `.env` and every state
> file only from verified payloads (a format 1 `.env` from the verified source archive).
> *Classes and levels.* A capture is a **healthy checkpoint**, an **emergency preservation** or **partial**; its
> verification level (`captured`, `failed`, `data-restore`) comes from a separate verification record under
> `artifacts/verifications/`. Only a healthy checkpoint is a rollback target.
> *Emergency.* `pf backup --emergency` (terminal only, `EMERGENCY BACKUP <project>`) preserves the actual data when
> a healthy checkpoint is refused, and every `rollback` now preserves the current database first and refuses
> (`preservation-failed`) when it cannot.
> *Downgrade.* Going back to the PF-A2.3 control is **unsupported** once a schema-1 bundle exists (its listings,
> rollback selection and purge fail on the new manifests).
> This block **supersedes** the checkpoint contents of section 10 and the `Deployed source` line of section 8.

> **Warning (PF-A3.2).** Deployment Admin checkpoint PF-A3.2 (2026-10-07) — operation journal, `resume` and
> `--abandon`, workspace generation switch; still a **development state, not a NAS release**. Offline, filesystem and
> installed-CLI evidence with a fake Docker daemon only: no real DSM host, Docker daemon, PostgreSQL server or SMB
> client was used.
> *Journal.* Every `deploy`, `update`, `rollback`, `reset-db`, `backup`, `purge`, `restore-instance` and `abort-deploy`
> writes a frozen plan and a journal under `<root>/instances/<uuid>/operations/<operation-id>/` (`plan.json` once,
> then fsynced `journal.json` generations; `attempts.json`, `children.json`, `deletion-progress.json`, the frozen
> `admin-config.json` and the frozen `app.env`). The former `state/pending.json` is used only by `pf permissions apply`;
> any other `pending.json` is refused (`journal-format-unsupported`). Every effect is journaled as intended before it
> starts and complete only after its result was observed.
> *Resume.* `pf resume [--operation ID] [--abandon | --keep-workspace]` re-enters the open operation: it re-checks the
> approved authorities, refuses while a recorded child, an owned one-off container or a database session of the
> operation still runs (`effect-still-running`; it never stops one), observes the unresolved effect and then either
> reopens the unchanged deployment (nothing data- or source-related started), continues forward, or stops in
> `needs_operator` when retrying could repeat a change. `purge`, `restore-instance <same bundle>`, `abort-deploy` and
> `backup` re-enter their own open operation the same way.
> *Workspace.* `repo/` is no longer replaced in place: after activation the deployed tree is staged next to it and
> switched by two renames; the old tree is kept, never deleted, as a retained generation in
> `<workspace parent>/.pf-generations-<instance-uuid>/`.
> *Diagnostics.* `status`, `doctor` and `instances` show open operations from the protected files before anything else;
> `pf status --operation ID` shows one operation in detail.
> *Downgrade.* Going back to the PF-A3.1 control is possible only with no open operation; it loses the journal display
> and the generation switch, and it treats journal-reconciled runner records as unresolved again.
> This block **supersedes** the A3.1 wording "PF-A3.2 cleans it" (section 7) and the in-place workspace replacement of
> sections 8 and 9.

> **Warning (PF-A3.3).** Deployment Admin checkpoint PF-A3.3 (2026-10-08) — integrated operations: exact-bundle
> functional verification in an isolated topology, side-by-side recovery targets, current-data protection in
> `reset-db`/`rollback`/`abort-deploy`, the capacity model, `pf cleanup` and the runner-record acknowledgement; still a
> **development state, not a NAS release**. The functional verification and the isolated topologies are proven
> **offline with a fake Docker daemon only**: no real Docker network, port, egress or PostgreSQL behaviour was
> observed, and isolation is proven from the daemon's reported configuration only (PF-A3.4).
> *Instance purge.* Before any deletion the exact purge bundle is restored into a throwaway `pfverify-<12 hex>` Compose
> project (own volume, internal network, no published port, generated database password) and checked functionally;
> the deletion gate requires that `functional_recovery_verified` record. After the deletion the registry record is
> marked `state: purged` and releases its project claim (section 12).
> *Recovery.* `restore-instance` accepts only this instance's own bundles (`restore-target-mismatch`), and
> `--side-by-side` now creates a kept, isolated `pfrecover-<12 hex>` recovery target instead of a `pf_recovery_*`
> database. `pf cleanup` reports and removes recorded leftovers; `pf resume --operation <op> --acknowledge` closes
> runner records of operations without a journal (sections 15 and 16).

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

Since PF-A2.3 the permissions of a registered instance follow one **semantic permission policy**. Nothing
asks for or accepts an octal mode, `+x-r` or a second numeric representation: modes are outputs shown on
request. The v2.5 rows for `control/` and `.pf-state-*` no longer apply: the control release lives in
`<root>/releases/<id>/` and private state in `<root>/instances/<uuid>/`.

### Choices and scopes

Each scope with a group gets one of three choices:

| Choice | JSON value | Meaning for members of the group |
| --- | --- | --- |
| Read and edit | `read_write` | read, create, edit, rename and delete |
| View and copy | `read_only` | read and copy |
| No group access | `none` | nothing; only the owner |

| Scope | Path | What can be chosen | Floor (never changed by a choice) |
| --- | --- | --- | --- |
| Workspace | registered `workspace` | group, any choice, inheritance, script execution `Owner only` or `Owner and group` | existing editor owners are kept; execute bits only for files the deployed-source manifest marks executable |
| Configuration | registered `configuration` | group, any choice, inheritance | no executable option |
| Control release | `<root>/releases/<id>/` | fixed: No group access | checked, never changed by `apply`: owner uid 0, no group/other write, no special bits |
| Backups | registered `backups` | group, View and copy or No group access | owner uid 0, no group write, no imported owner |
| Recovery bundles | registered `recovery` | group, View and copy or No group access | as backups |
| Private state | `<root>/instances/<uuid>/` | fixed: owner only | directories `0700`, files `0600`, no group |

Mode table (audit detail; the owner always keeps read/write, `other` never gets a bit, setgid only on
directories with inheritance, setuid and sticky never):

| Access | File | Directory | Directory with inheritance |
| --- | --- | --- | --- |
| No group access | `0600` | `0700` | `2700` |
| View and copy | `0640` | `0750` | `2750` |
| Read and edit | `0660` | `0770` | `2770` |

A file the protected source manifest marks executable gets the file mode plus owner execute (`Owner only`)
or owner and group execute (`Owner and group`). No rule looks at a file extension, and nothing under `.git`,
`node_modules` or another excluded name is ever designated. "No script execution" is not offered for the
workspace: files marked executable in the deployed source keep an owner execute bit so the workspace still
matches its source manifest (`permission-policy-unsupported` if such a policy is ever submitted).

### Permission policy revision, derived policy and proposals

"Permission policy revision N" is the approved permission policy of one instance. It is unrelated to the
"approved policy revision" of the environment policy (`staging` revision 1), which no permission command
changes.

- **Approved.** A confirmed `pf permissions apply` that verified every selected scope writes revision N+1 to
  `<root>/instances/<uuid>/permission-policy.json` (root, `0600`). A byte copy stays in that apply's operation
  directory, so the chain of revisions can be walked back to revision 1. `purge` keeps the record; an instance
  registered anew starts without one (it is not part of recovery bundles).
- **Derived (not approved yet).** Workspace and configuration: `workspace_write_group`, Read and edit,
  inheritance, `Owner and group` script execution (the A1/A2.2 modes `2770`/`0660`/`0770`). Control: No group
  access. Backups and recovery: View and copy for the group that currently owns the backups and recovery
  folders. Only root can change that group (`validate_context` requires the folders to be owned by root and
  not group-writable). A folder gid without a group name stops with `permission-group-missing` until `apply`
  approves a named group.
- **Proposals.** `backup_read_group` in `pf-config.json` is a proposal for every instance: it never changes
  who can read backups or recovery bundles by itself. `workspace_write_group` is a proposal once a revision
  exists; before the first approval it still sets the group of files pf creates in the workspace and the
  configuration (declared limit). `doctor`, `check`, `plan` and the `apply` wizard show a differing proposal
  (`permission-group-proposal`); it takes effect only when `apply` writes a new revision.
- A members-of-the-backup-group warning is shown wherever that group is chosen:
  members of the backup read group can read database contents and any credentials included in a backup or
  recovery bundle.

### `check`, `plan` and `apply`

```sh
sudo pf --instance <slug> permissions check [--scope SCOPE]...
sudo pf --instance <slug> permissions plan  [--scope SCOPE]... [--details]
sudo pf --instance <slug> permissions apply [--scope SCOPE]... [--details]
sudo pf --instance <slug> permissions apply --resume | --abandon
```

`SCOPE` is `workspace`, `configuration`, `control`, `backups`, `recovery` or `private_state` (repeatable;
default all).

- `check` and `plan` are read-only: no lock, no operation directory, nothing written anywhere. They map the
  protected-context findings onto scopes: a finding at, above or below a workspace, configuration, backups or
  recovery folder blocks that scope (`scope-path-unsafe`) and the others are still reported (without an approved
  policy, a missing or unsafe backups or recovery folder shows "group unavailable": its group is not read from it);
  any other refused finding, and one at or above the installation root (even when it is above a scope folder too),
  stops with `permissions-context-refused` before any folder is read. `check` exits 1 when an entry
  differs or the control release breaks its ceiling (`permissions-differ`).
- `plan` prints, per scope, the policy, the group members (at most 20; primary-group and directory-service
  members are not listed), the counts (group, mode, special bits), whether a freeze is needed, the blockers
  and, with `--details`, each change in octal and symbolic form. `Plan hash` is the sha256 of the exact change
  list; `apply` shows the same hash for the same policy on an unchanged tree. With a differing
  `pf-config.json` proposal, a second block shows the candidate that applies it.
- `apply` takes the instance lock, asks the numbered wizard (group, access, inheritance and, for the workspace,
  script execution; Enter keeps the shown default, `q` cancels), plans, shows the policy changes and the plan
  hash, and asks one typed confirmation `APPLY PERMISSIONS <slug>`. It then revalidates the context, the
  record, the groups and the folder identities, records its intent (`permission-plan.json`,
  `permission-changes.jsonl` and `state/pending.json`), applies and verifies every selected scope, and only
  then writes the new revision. An unchanged approved policy on a compliant tree is `permissions-current`
  (exit 0, nothing written).

The apply never follows a link, never crosses a mount, never changes a hard-linked, special, ACL-bearing or
`@docker` entry, never chowns an untrusted owner into a protected scope and never changes the control release:
each of these is a blocker reported by `plan`, and `apply` changes nothing while one exists.

### Editor freeze

A change below the root of the workspace or the configuration (a bulk change) needs a verified editor freeze.
`apply` first sets every such scope root to owner-only (keeping its setgid bit), so editors can no longer enter
the folder, then takes the authoritative inventory of the fenced folders and scans `/proc` for any other process
whose working directory, root or open file is an entry of the plan or of that inventory (so an entry an editor
created while the plan was being read counts too). A holder lifts the fence again and refuses with
`editor-freeze-refused` (nothing changed). The
scan also reports the shell that started the command: **start `apply` from outside the scope folders, for
example `cd /`**. The freeze is unavailable when `/proc` cannot be read or the scope root carries an ACL;
`plan` then reports `editor-freeze-unavailable` and bulk apply of that scope is blocked. Deselect the scope
with `--scope`, or change it by hand: stop SMB editing of the folder, apply the modes of the table above,
then run `pf permissions check`. A root-only change needs no freeze. A userspace lock never locks SMB
clients, and the fence does not stop a root service or a memory-mapped file (declared limit).

### ACLs and DSM

`apply` never writes or removes an ACL entry. An entry with an access ACL, or an ACL this control cannot
read, is a blocker (`scope-entry-acl`): changing its mode would rewrite the ACL mask. A directory with only a
default ACL is allowed and reported under future-file behaviour. DSM Shared Folder permissions still apply
and POSIX modes do not override a DSM deny: verify the Shared Folder permissions for each SMB account from the
share root (DSM Control Panel), not only from an SSH shell. DSM shares with ACLs block bulk apply until
PF-A5.1 supplies a DSM adapter.

### Three statuses

`check` and `apply` print three statuses per scope and never one green line: `mode_applied`
(`yes`, `partial (N differ)`, `no`, `not-selected` or `check-only`), `effective_access_verified` and
`future_file_behavior_verified`. The last two are always `not verified` in this checkpoint: they are computed
from mode bits only. Setgid on a directory carries the group, not write access; files created over SMB take
the client's umask or the share's create mask, and default ACLs can change them. Files created by pf get
explicit modes and are verified after creation.

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

Since PF-A2.1 the installed control plane is a protected installation root:

```text
<root>/
├── bootstrap/            # pf (launcher), pf_bootstrap.py (verifier), bootstrap.conf, tools.conf,
│                         # backup.sh, release-check.sh (scheduler wrappers)
├── releases/r-<16 hex>/  # immutable, content-addressed control releases (control-manifest.json)
├── install-operations/   # one plan.json + journal.json per install operation (kept as the audit record)
├── profiles/             # partflow-staging-legacy.json
├── policies/             # staging.json
├── registry/, locks/, instances/<uuid>/, staging/, sources/, home/
```

`install-control.sh init` is the trust boundary: the administrator chooses and runs these reviewed
repository bytes. It reads the candidate files as data (no-follow, size-bounded, parsed as Python 3.9,
literals only) and runs new code only in the smoke check after the typed confirmation; what is installed
is exactly what the summary showed. Every later change runs from the installed, verified control
(`<root>/bootstrap/pf install …`), never from the repository.

After installation, the repository `pf.sh` intentionally refuses operational execution.
Use:

```sh
sudo <root>/bootstrap/pf status
```

(or `sudo pf status` when `init` created the global launcher for this root), not:

```sh
sudo sh ./pf.sh status
```

Application updates do **not** silently update the privileged control plane. If a later
PartFlow revision changes `pf-admin.py`, `compose.nas.yaml`, or another lifecycle source,
review that revision and install it explicitly with `pf install control` (section 15).

## 5. Install or migrate

Every command below that names `<root>/bootstrap/pf` may be written `sudo pf …` only when the global
launcher was created by `init` for this root. On a NAS that still runs v2.5, `/usr/local/bin/pf` is the
v2.5 launcher (or another root's) and does not reach the new root.

### (a) New installation

1. From a reviewed repository checkout, initialize a new root (absent or empty):

   ```sh
   sudo sh ./deploy/synology/install-control.sh init --root <root>
   ```

   Missing inputs are asked; `--interpreter`, `--tool <id>=<path>`, `--launcher-path <path>` and
   `--no-launcher` override the detected defaults shown in the summary. Confirm with
   `INSTALL CONTROL <release-id>`. Any other verb of `install-control.sh` is refused with
   `installer-verb-installed-only`. An existing empty root must be on its parent's filesystem: a mount
   point or a DSM shared folder itself is refused (`root-exists`), because the root is published with one
   atomic rename of a sibling build directory; name an absent path below it instead. If the publication
   fails or is interrupted, nothing was published: the build directory is removed and you run the same
   `install-control.sh init` again (there is no `resume` for a root that does not exist yet).
2. Create the admin configuration with the pre-registration wizard (the directory must already exist,
   root-owned, in protected ancestors; nothing is registered yet):

   ```sh
   sudo <root>/bootstrap/pf config admin --configuration <config> --project <project>
   ```

   It asks the workspace and backup groups from the host's existing groups, shows the summary and writes
   `<config>/pf-config.json` (schema 2, `root:<workspace group>`, `0660`) after `y`. It refuses a directory
   that an open install operation, a pending registration or a registered instance uses
   (`install-operation-pending`, `registry-pending`, `config-path-conflict`), and refuses any directory while
   a registered instance record cannot be loaded (`registry-record-invalid`). An existing legacy schema 1 file
   is left as it is (`admin-config-legacy-unregistered`). The completion line of `init` names this command.
3. Register the instance with its existing directories:

   ```sh
   sudo <root>/bootstrap/pf install register --slug <slug> --project <project> --workspace <checkout> \
     --configuration <config> --backups <backups> --recovery <recovery>
   ```

   The preflight checks the paths (nothing is created), the admin configuration and its groups, the
   managed-path inventory and the Docker daemon (one read-only `docker info`). Confirm with
   `REGISTER <slug>`. The registry default is never changed.
4. Create `.env` with `sudo <root>/bootstrap/pf --instance <slug> config app`, or let the first
   `pf --instance <slug> deploy` ask the same questions.

### (b) v2.5 home

```sh
sudo <root>/bootstrap/pf install migrate-legacy --legacy-home <home> --workspace <checkout> --slug <slug>
```

- `configuration`, `backups` and `recovery` are `<home>/config`, `<home>/backups` and `<home>/recovery`;
  the workspace is the checkout you name, whatever its name; the project comes from the effective
  `pf-config.json`.
- Conflicts are never resolved automatically: when `<checkout>/.env` and `<home>/config/.env` (or
  `<checkout>/deploy/synology/pf-config.json` and `<home>/config/pf-config.json`) both exist and differ,
  the preflight refuses and names both. A legacy-only file is copied byte for byte (source mode and group)
  into a private staged name, validated, then published without overwriting; the legacy copy stays where
  it was (v2.5 still reads it).
- The v2.5 operation lock `<home>/.pf-state-<project>/operation.lock` is held during the operation; a v2.5
  `pending.json` refuses the migration (finish or resolve it with v2.5 first). The other v2.5 state files
  are reported, not imported.
- What is migrated: the configuration copies and the registration. What is not: v2.5 state, Docker
  resources (no adoption), permissions, the v2.5 `control/` directory and the v2.5 launcher.
- v2.5 stays the control plane for every change to the instance; pf gives read-only views (`status`,
  `doctor`, `ps`, `logs`) and refuses mutation with `legacy-control-active` until legacy adoption is
  installed.
- A migrated instance keeps its schema 1 `pf-config.json` (v2.5 reads the same file and would refuse
  `schema_version`). `pf config` is refused like every mutating route (`legacy-control-active`) until
  adoption, and the instance counts as deployed for `pf config app` (its database exists).

> **Warning (PF-A2.1).** This is a development checkpoint. A live v2.5 NAS is not migrated before the PF-A2
> adoption sub-slice (OD-A21-05) and the unattended-grant decision (OD-A14-13) exist. DSM shared-folder ACLs
> and a group-writable `backups/` are refused by the preflight until PF-A2.3/PF-A5 (A1-T17).

> **Warning (PF-A2.2).** The config wizards are a development checkpoint, proven offline only. A configuration
> file that carries ACL entries (a DSM shared folder usually does) is refused (`config-file-acl`) and must be
> edited by hand until PF-A2.3. A password that needs URL encoding still fails at the database migration step of
> `deploy` and `update` (section 6, `.env`).

## 6. Configuration files

### `config/pf-config.json`

This is the actual NAS-local administration configuration. `pf config admin` creates, migrates or
completes it (section 5 (a) before registration, `sudo pf --instance <slug> config admin` after it);
`pf install migrate-legacy` only copies an existing legacy file byte for byte.

Schema 2 template (the installed `pf-config.example.json`, immutable input of the wizard):

```json
{
  "schema_version": 2,
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

- **Two schemas.** A file without `schema_version` is legacy schema 1: every omitted key takes its frozen
  schema 1 value (the values of the template above), and it keeps working everywhere. Schema 2 lists
  every key; a missing, unknown, duplicate or mistyped key is refused. `branch` must be a branch name
  (no `..`, `//` or trailing `/`), `ci_workflow` a workflow file name, the groups names without control
  characters, `:` or surrounding whitespace. Any other `schema_version` (including an explicit `1`) is
  `admin-config-version-unsupported`.
- **Migration is explicit.** Only `sudo pf --instance <slug> config admin` turns schema 1 into schema 2: explicit
  values are kept and the implicit ones are written as the frozen schema 1 values, never as the current
  example. A value that schema 2 refuses blocks the migration (`admin-config-migration-blocked`) and is never
  repaired. Neither `pf install control` nor `migrate-legacy` changes the file. An unregistered directory (it may
  be a v2.5 home's `config/`) and a v2.5-active instance are never migrated.
- **Questions.** The wizard asks only `workspace_write_group` and `backup_read_group`: both when it creates the
  file, otherwise only a group that does not exist on this host (no default; a broader group is never picked
  silently). The answer is a number from the list of the host's groups (`users`, then groups with gid 1000 or
  more) or the exact name of any existing group. Groups are never created. `branch`, `ci_workflow`,
  `release_channel`, `auto_update`, `health_timeout_seconds` and `minimum_free_mb` are shown but not asked;
  change them by hand.
- **Environment label.** `environment` must equal the approved policy environment of the instance. It is a
  label: changing it never changes the policy (`admin-config-mismatch`); a policy change is a separate approval
  (PF-A4.3). `project` must equal the registered Compose project.
- **Groups are proposals (PF-A2.3).** `backup_read_group` is always a proposal: backups and recovery
  bundles keep the group of their folders, or the group of permission policy revision N, until
  `pf permissions apply` approves a change. `workspace_write_group` becomes a proposal once a permission policy
  revision exists; before that it still sets the group of files pf creates in the workspace and the
  configuration. The wizard summary states which case applies.
- **Writes.** The file is replaced through a private temporary `.pf-config.json.pf-config-<8 hex>` (for `.env`:
  `.env.pf-config-<8 hex>`) in the same directory, keeping the owner, group and mode of the file it replaces; a
  new file of a registered instance gets the configuration target of the permission policy in force (owner root,
  the configuration group, `0660`, `0640` or `0600`) and is checked after creation; before registration it is
  `root:<workspace group>` `0660`. If the file changed after the summary, nothing is written
  (`config-changed`). These temporary names are reserved: a leftover of an interrupted run is removed by the
  next run, and a file with such a name that is not a leftover is refused (`config-file-unsafe`), never removed.
- **Downgrade.** A control release older than PF-A2.2 refuses `schema_version`. Selecting such a release with
  `pf install control --release` after a migration stops at its smoke check (`install-smoke-failed`), and nothing
  is bound.

`auto_update` is a proposal only: unattended apply needs a protected policy grant, which this
checkpoint does not provide (section 13).

### `config/.env`

`sudo pf --instance <slug> config app` creates or completes it for the instance's profile, and the first
`deploy` without `.env` runs the same wizard (inside `deploy` no `config-change.json` is written; the deploy
operation's `operation.json` and frozen `app.env` snapshot are the evidence). A created file starts from the
installed `nas.env.example` (comments kept); otherwise only the lines that change are rewritten and every
other line keeps its exact bytes. The wizard needs a valid `pf-config.json` (`admin-config-required`). It asks
only missing or invalid values, in this order: PostgreSQL user, PostgreSQL database, factory timezone, access
mode (and the LAN address), HTTP port, and the Reverse Proxy hostname (asked only for the `127.0.0.1`
binding, with no default; Direct LAN uses `localhost`). When the access mode is asked and you choose the
DSM Reverse Proxy, a kept `PARTFLOW_ALLOWED_HOST=localhost` is asked again, because the proxy needs the
exact internal hostname.

- **Secrets.** An existing `POSTGRES_PASSWORD` is never shown, changed, regenerated or re-quoted (summary:
  `unchanged`). A missing or empty one is generated as 64 hexadecimal characters from cryptographically
  secure randomness (summary: `set`) only when the instance was never deployed.
- **Deployed instances.** An instance counts as deployed when `state/deployed.json` exists, when a completed
  v2.5 migration pins it, or when its state cannot be read. Then `POSTGRES_USER`, `POSTGRES_PASSWORD` and
  `POSTGRES_DB` are never asked, generated or rewritten; a missing or unusable one is refused
  (`app-credential-unusable`) with restore guidance. A full `purge` removes the state, so a purged instance
  is new again; a purged migrated instance stays deployed.
- **Password length.** A kept password shorter than 32 characters is reported
  (`password-weak-for-new-deployment`): the first `deploy` refuses it. Set a longer one by hand, or leave the
  line as `POSTGRES_PASSWORD=` (empty) and run `config app` again to generate one.
- **Timezone.** A new or missing `SITE_TIMEZONE` must exist in this host's installed zone data. An existing
  value that the host does not know is kept with a note (`zone-unknown-on-host`); an existing value that is
  not a zone name at all (a `.` or `..` component) is asked like an invalid one; without host zone data an
  existing value is kept with a note and a missing one is refused (`zone-data-unavailable`). The backend
  checks the value with its own image's zone data at startup; that data is not checked by the wizard.
- **ACLs.** A `.env` or `pf-config.json` that carries ACL entries is refused before the first question
  (`config-file-acl`): replacing it would drop the ACL. Edit such a file by hand; an ACL-preserving replacement
  belongs to PF-A5.1. A created `.env` gets the configuration target of the permission policy in force; the
  reuse of an existing `.env` by the first `deploy` sets the same target on that file only.
- **Database URL.** The controller passes `PARTFLOW_DATABASE_URL` with percent-encoded credentials; nothing
  is spliced raw. Limit (until the app-lane fix of `backend/alembic/env.py`, P16-S3 or later, is deployed): the
  backend migration step hands that URL to Alembic's configuration parser, which rejects `%`. A password
  that needs encoding (any character outside letters, digits and `-._~`) therefore makes `deploy` and `update`
  stop at the migration step, before the new version is activated. Generated passwords are unaffected; an
  existing password is never changed to work around it.

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
workflow or plan a credential/database migration explicitly; no wizard rotates credentials.

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
2. Creates/reuses `config/.env` (with the configuration permission target, PF-A2.3).
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

Since PF-A3.1 step 7 is preceded by a capacity check of the deployment artifact store (`artifact-capacity`: the
source size plus 64 MiB must be free in `<root>/instances/<uuid>/artifacts/deployments`) and followed, before any
change, by **staging** the deployment: the exact source tree is archived with its manifest into a private
`.staging-<id>` folder. A staging failure (`deployment-stage-failed`) leaves the application, database and workspace
unchanged. After the health checks pass the staging is **sealed**: the database image identity, the resolved
Compose model and the frozen `.env` are added, the folder is renamed to its deployment ID and `deployed.json` gains
`deployment_id` and `deployment_record_sha256`. The record holds secrets and is root-only; it is never placed in a
checkpoint (checkpoints refer to it by ID and hash) and it survives a checkout change, the loss of `repo/.git` and
`purge`.

A seal failure after a healthy activation does not stop the application: the operation is closed, `deployed.json`
records `deployment_seal_failed` instead of a record, the command exits 1 with `deployment-record-incomplete`, and
`status` reports `Deployment: not recorded (…)`. The next `deploy`, `update`, `rollback` or `restore-instance` seals a
record. The seal is not retried inside the same operation: when its workspace refresh was still pending, a later
`resume` finishes the refresh and closes the operation `failed_preserved` with the same exit 1 and copy.
A staging folder left by an interrupted run is reported by `status` (`Unsealed deployment staging: N`) and is
never used; PF-A3.2 cleans it.

Since PF-A2.3 the new source tree is copied content-only and only the names `deploy` copied get the workspace
targets. The copy works through held, no-follow folder handles on both sides: a source entry swapped for a link
or another file, or a new folder an editor swaps for a link while it is filled, stops the copy before anything is
read or written through it; `deploy` without a selector (`--current`) changes no workspace permission and prints
`Workspace permissions were not changed; check them with '<pf> permissions check --scope workspace'.`

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

Since PF-A3.2 the deploy writes its plan and journal right after `DEPLOY <SHA12>` (section 16, Incomplete lifecycle
operation). The effects run in this order: staging, database start, initial migration, backend, frontend, seal,
`deployed.json`, then the **workspace refresh** (section 8). An interrupted deploy is continued with
`sudo pf --instance <slug> resume`; `abort-deploy` is legal only until the frontend effect started (afterwards it is
refused with `operation-open` and `resume` completes the deploy). A lost result of the initial migration stops in
`needs_operator`; `abort-deploy` then supersedes the deploy and removes only its own frozen resources, after which
`deploy` can run again. The private staging of a cancelled operation is removed (`note: staging-removed`); an
unreferenced `.staging-*` folder is removed by the next staging flow, except the staging of a running deployment
whose seal failed (`note: unsealed-active-staging`). The staging of a superseded operation stays while the recovery
that superseded it is open and is removed by the next staging flow once that recovery (or the last recovery of a
chain of them) completed.

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
Deployment: <deployment-id> (git_commit <sha12>|unknown), sealed <stamp>
```

Since PF-A3.1 the deployed source comes from the **deployment record** (section 7), not from `repo/` or its
`.git`. `Deployed source:` prints the commit only when the protected source store proved it; otherwise it prints
`unknown provenance (…)` and never a claimed commit. `deployed.json` `sha` is likewise written only for a proven
commit (`null` otherwise); a pre-A3.1 `sha` written by a `rollback:` or `restore:` counts as the deployed commit only
when the protected source manifest records that same commit. `pf release-check` prints the same `Deployed source:`
line. The `Deployment:` line is one of:

```text
Deployment: <id> (git_commit <sha12>|unknown), sealed <stamp>
Deployment: legacy (no deployment record; created before PF-A3.1)
Deployment: not recorded (seal failed in operation <op>; the next deploy/update/rollback seals one)
Deployment: <id> deployment-artifact-mismatch: <file>: <detail>
```

followed by `Unsealed deployment staging: N` and `Unreferenced deployments: N` when they are not zero. A deployment
whose files differ from its record is kept as evidence and never used as a source (section 16).

No lifecycle command changes the permissions of existing workspace files (PF-A2.3); `pf permissions check
--scope workspace` reports them and `pf permissions apply` changes them under an editor freeze.

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

**Workspace generation switch (PF-A3.2).** `deploy`, `update`, `rollback` and `restore-instance` no longer copy the new
tree into `repo/`. After activation, seal and `deployed.json`, they:

1. stage the deployed tree as `stage-<generation>` inside the generation container
   `<workspace parent>/.pf-generations-<instance-uuid>/` (root, `0700`, same filesystem as `repo/`),
2. rename `repo/` to the retained generation `<container>/<generation>` (`wsg-<stamp>-<8 hex>`),
3. rename the stage to `repo/`,
4. rewrite the protected source manifest of the new workspace.

The old tree is **kept** as a retained, unsealed generation; nothing in it is deleted, also untracked files and its
`.git`. A file an editor had open, or saves while the switch runs, lands in the retained generation, not in the new
`repo/`. Copy such work back as root, for example
`sudo cp -a /volume1/partflow/.pf-generations-<uuid>/<generation>/<path> /volume1/partflow/repo/<path>`; the tool never
runs Git or any other program inside a retained generation, so its `.git` hooks or `fsmonitor` settings are inert.
`pf status` counts the retained generations (`Workspace generations: N unsealed in <container> (latest <generation>
from operation <op>)`) and reports a leftover `stage-*` as `in progress` or `superseded-stage`; retention is PF-A5.1.

Between the two renames `repo/` is briefly absent. If the process is interrupted exactly there, every command except
`resume` is refused (the registered path is missing) and `status` prints `workspace: the registered workspace path may
be absent between the two renames of the workspace switch; only 'resume' (or 'resume --keep-workspace') may continue`.
`sudo pf --instance <slug> resume` binds the staged tree; `resume --keep-workspace` renames the old tree back instead.

The switch is unavailable — the operation then completes activation, seal and pointer, exits 1 with
`workspace-sync-pending` and stays open in `workspace_sync_pending` with the application running — when `repo/` is a
mount point, subvolume or shared-folder root (`workspace-is-mount-point`), the container is unsafe or collides with a
registered path (`generation-container-unsafe`, `generation-container-collision`), `repo/` itself carries an ACL
(`workspace-root-acl`) or the device lacks room for the staged tree plus `minimum_free_mb`
(`workspace-capacity`). Fix the cause and run `resume`, or run `resume --keep-workspace` to keep the current
workspace (its manifest and provenance are then left as observed). A manual `pf backup` stays possible while the
switch waits untouched. While that backup is open (it was interrupted), the waiting operation is held: `status` adds
`waits: backup operation <op> is open; run 'pf --instance <slug> resume --operation <op>' (or add --abandon) first,
then this operation's routes apply`, and `resume --operation <switch-op>` (also with `--keep-workspace`) is refused
with `operation-open` naming the backup's `resume --operation <backup-op>` and its `--abandon`. Once the backup is
closed, `resume` or `resume --keep-workspace` of the waiting operation apply again.
`deploy`, `update`, `rollback` and `restore-instance` also accept `--keep-workspace` to skip
the refresh from the start; the confirmation summary shows the choice and the number and size of retained
generations.

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

Since PF-A3.1 the update checks, before its `UPDATE` confirmation, that the running backend/frontend images are the
ones of the current deployment record (`deployment-image-mismatch`) and that the deployment artifact store has room
(`artifact-capacity`); it stages the new deployment right after the confirmation, before application writes stop,
and seals it after the health checks (section 7). The current commit is read from the deployment record (then from a
proven `deployed.json` `sha`). When it is unknown, a **manual** update still runs — its pre-update checkpoint takes
the source from the deployment record — while an automatic update is deferred (`Automatic update refuses a
deployment whose source commit is unknown; run a manual update.`). The pre-update checkpoint is a schema-1 healthy
checkpoint with a verification record (section 10).

**Phases and resume (PF-A3.2).** An update runs `preparing` (staging), `preserving` (stop, pre-update checkpoint),
`migrating` (rehearsal candidate restore, rehearsal migration, candidate drop, live migration), `activating`
(backend, frontend, seal, pointer) and `syncing-workspace` (section 8). After an interruption `pf resume`:

- **before any database effect** (staging, stop, checkpoint, rehearsal candidate): drops the operation's own candidate
  by its planned name, reopens the unchanged deployment and closes the update `cancelled` (the former `pf resume`);
  `pf resume --abandon` does the same (`ABANDON <op8>`);
- **after a live database effect**: continues forward from the journal;
- when the **live migration's result was lost**: reads the live Alembic heads. Heads equal to the target count as done
  (the upgrade is never repeated); heads still at the previous revision, or any other heads, stop in
  `needs_operator` (`effect-unknown`) because a partial non-transactional change cannot be excluded. The supported
  route is then lossless: `sudo pf --instance <slug> rollback <before-update checkpoint> --restore-db` supersedes the
  update, preserves the current database first and restores the checkpoint. The update is then listed as
  `superseded`.

## 10. Backups and rollback

Create a verified revision checkpoint:

```sh
sudo pf backup
```

A checkpoint contains:

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

**Manifest schema 1 (PF-A3.1).** A new checkpoint's `manifest.json` follows the `recovery_manifest` record of
`deploy/synology/contracts/lifecycle-records.schema.json`; `manifest.sha256` is the SHA-256 of the exact file bytes.
It records:

- the **capture class**: `healthy_checkpoint` (the live schema matches the deployed images, the exact deployed source
  is known and every image is identified), `emergency_preservation` (the actual data, with the mismatch recorded as
  evidence) or `partial` (an emergency capture where an image or the source could not be identified);
- quiescence (whether backend/frontend were running), the source origin and provenance, the deployment it binds to
  (ID and record hash; the record itself is never copied into a checkpoint because it holds secrets), the
  backend/frontend/db image identities (ID, platform, repo digests), the PostgreSQL server version, and per database
  store its owner, encoding/collation/ctype, extensions, Alembic heads and — only when writers were stopped (before
  `update`, `reset-db`, `rollback`, `purge`) — exact row counts; `pf backup` with running writers records none;
- every payload with its type, size and hash, plus the exclusions and manual prerequisites that a restore needs.

**Verification level.** The restore test writes a separate **verification record** in
`<root>/instances/<uuid>/artifacts/verifications/<bundle-id>/` (root only); the bundle is never edited afterwards.
The level shown by `backup`, `backups` and `recoveries` is computed from these records: `captured` (sealed, no
record yet), `failed` (the last restore test failed), `data-restore` (every store restored from the bundle's own
dump, with heads, locale and row-count checks) and `functional` (since PF-A3.3: the exact purge bundle or a
side-by-side target restored and checked in an isolated topology, section 12). A broken
record is reported as `note: verification-record-invalid: …; ignored.` and does not raise the level. `pf backup` now
prints:

```text
Checkpoint class: healthy_checkpoint
Verification level: data_restore_verified (record <verification-id>)
```

**Strict reading.** Every consumer (listings, rollback, purge, restore) first reads the bundle strictly: the manifest
bytes must match `manifest.sha256`, the manifest must validate, and every payload must be a regular, single-link file
of the recorded size and hash; a file that the manifest does not list is refused. Nothing is extracted, confirmed or
journaled before that. Archives are then extracted by a safe importer that refuses every link or special file,
absolute or `..` paths, duplicate or oversized members and archives that change while being read
(`archive-member-refused`, `archive-unreadable`, `archive-changed`, `archive-capacity`).

**Before a capture.** `pf backup`, `update`, `reset-db` and `purge` refuse a healthy checkpoint before any change
when the running backend/frontend images are not the current deployment's (`deployment-image-mismatch`), or when the
deployed source cannot be proven at all (`deployment-artifact-mismatch` refusal, or the existing "Cannot reconstruct
the exact deployed source revision" refusal for a deployment without a record). When only the deployment record's
files are damaged but the source can still be proven from the protected source store or the workspace, the capture
proceeds with a `note: deployment-artifact-mismatch: …` and records the record as excluded.

**Emergency preservation.** When a healthy checkpoint is refused but the data must be kept, run, interactively:

```sh
sudo pf --instance <slug> backup --emergency
```

It prints the observed contract (live heads, image heads, running and expected image IDs), asks
`EMERGENCY BACKUP <project>`, captures and restore-tests the current database and prints
`Emergency preservation <id> captured (<level>). It is evidence and data for repair or export, not a rollback
target.` When the drifted workspace exceeds the archive limits (section 16, `workspace-archive-limit`) it still
preserves the data and records the workspace as excluded. It is refused without a terminal and is never run by
`backup.sh`. While a deploy, update, rollback or
reset-db is interrupted, `status` lists it as a legal route; it does not change the interrupted operation. An
emergency or partial capture is **never a rollback target** (`checkpoint-not-rollback-target`).

**Rollback preservation.** Every `rollback`, code-only or `--restore-db`, now preserves the current database after
application writes stop and before anything is restored or switched: a healthy checkpoint when the live contract
matches, otherwise an emergency preservation. If the current database cannot be captured **and** restore-tested, the
rollback stops with `preservation-failed`: nothing was restored or switched, the services stay stopped, and
`pf resume` reopens the unchanged deployment (section 16 gives the manual `pg_dump` step).

**Rollback checks.** The selected checkpoint must be a healthy checkpoint; its backend/frontend image IDs must still
be retained; a different database image only prints `note: db-image-changed: …` when the PostgreSQL major matches.
The source is extracted with the safe importer and compared with the recorded source manifest
(`source-manifest-mismatch`). A code-only rollback does not use the dump, so a `failed` level is a note
(`note: verification-failed: …`). `--restore-db` restores into a candidate created with the store's encoding and
locale, checks heads, locale, available extensions, owner and (when recorded) row counts, writes a verification
record **before** the switch, and on any difference drops the candidate and refuses with `checkpoint-incompatible`
while the current database stays unchanged.

**Legacy checkpoints.** Format 1 and 2 checkpoints written before PF-A3.1 are read through an explicit, deterministic
migration (`note: legacy-manifest-migrated: <id> format <n> read as <class>; limitations: …`), never rewritten. A
format-2 checkpoint is healthy only when it recorded a verified source, a passed restore test, both images and the
migration fingerprint; it is listed with `legacy-format-2` and `source=claimed <sha12>`: the claimed commit is only a
hypothesis that the protected store must prove again before it is used. Every format-1 checkpoint reads as `partial`
(it was not rollbackable before either). No verification record is synthesized from a legacy `restore_test` claim:
the level stays `captured` until a `--restore-db` verifies it on its candidate.

Since PF-A2.3 a new checkpoint gets the backups target of the permission policy in force (the group of the
backups folder, or of the approved revision; View and copy gives directories `0750` and files `0640`), set
explicitly and verified. Only the new checkpoint is changed. A checkpoint entry that inherits an ACL from its
folder stops the backup (`fresh-entry-acl`) before it is published.

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

`pf backups` lists one line per checkpoint (PF-A3.1):

```text
<n>. <bundle-id>  [<healthy|emergency|partial>|<captured|failed|data-restore|functional>]  <reason>  DB=<heads>  source=<git_commit <sha12>|unknown|claimed <sha12>>  [legacy-format-N]
<n>. <folder>  [invalid: <code>]
```

**Journals for backups and rollbacks (PF-A3.2).** `pf backup` writes a plan (capture, then verification). A caught
capture or verification failure closes it `failed_preserved` (not blocking; the partial folder is listed as a
`bundle-attempt` and never selectable); only an interrupted process leaves it open, and `pf backup` or `pf resume`
then makes a new attempt with a new bundle ID (a half-verified `pf_verify_*` candidate of the plan is dropped
first). `pf rollback ... --restore-db` may **supersede** an open `update`, `rollback` or `reset-db` that stopped the
application (or is in `needs_operator`); a code-only rollback may supersede only an update without any database
effect, otherwise it is refused with `review recovery with --restore-db`. An interrupted rollback candidate restore
is dropped and restored again from the same checkpoint; a checkpoint that changed meanwhile is refused with
`plan-input-changed` before the `RESUME <op8>` confirmation (nothing is recorded). A superseding operation that is
resumed before it changed anything **withdraws**: it starts no service, and the superseded operation and its routes
apply again. `pf resume --abandon` of a rollback before its database switch drops its own `pf_restore_*` candidate,
then reopens the unchanged deployment (or, for a superseding rollback, withdraws and leaves the services stopped).

**Data choice and capacity (PF-A3.3).** The `rollback ... --restore-db` confirmation now ends with a data-choice line:
the active database returns to the selected checkpoint (with its creation time); writes after that time stay in the
retained `pf_keep_*` database and in the `before-rollback` preservation, and nothing is merged. The exact summary the
operator confirmed is kept as `operations/<op>/confirmation-summary.txt` (0600; its SHA-256 is the plan's
`confirmation.summary_sha256`) and `pf status --operation <op>` prints it. Before the confirmation every lifecycle
command measures the free space it needs per device (`statvfs`): backups, recovery, workspace, artifacts, private state
and the Docker root (`DockerRootDir` of the daemon). Needs of roles on one device are summed and the
`minimum_free_mb` floor is counted once per device; a shortfall refuses with `capacity-insufficient` and changes
nothing, and the measurement is recorded in `operations/<op>/capacity.json`. Every `pf backup` needs Docker-root space
for its restore test; when the Docker root cannot be measured it refuses with `capacity-unmeasurable` and names
`pf backup --emergency`, which preserves the database without that check. The need is re-checked before each effect;
pf never deletes anything to make room.

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

Since PF-A3.1 the `before-reset` checkpoint is a schema-1 healthy checkpoint with a verification record, and
`reset-db` first runs the same deployment image binding check as `pf backup` (`deployment-image-mismatch` before any
change).

Since PF-A3.3 `reset-db` decides from the observed contract before its confirmation:

- live heads equal to the running image's heads: the healthy `before-reset` checkpoint, as before;
- a **schema/image mismatch** (live heads differ from the running backend image's heads, and the running image is the
  one the deployment record names): the current database is captured as **emergency preservation** first (data and
  evidence, never a rollback target), then the clean database is migrated to the **running image's heads**;
- a **deployment-image mismatch** (the running backend image is not the one the deployment record names) refuses
  with `reset-deployment-image-mismatch`; converge the deployment first (`update` or `rollback`);
- an unreadable image contract refuses with `reset-contract-unknown`.

The confirmation summary names the preservation and the retained database (`pf_keep_*`, the previous active database,
which is never dropped) and is kept as `confirmation-summary.txt`. A preservation failure stops with
`preservation-failed` before any candidate or switch. No `alembic downgrade` is ever run.

Since PF-A3.2 an interrupted `reset-db` drops its own `pf_clean_*` candidate and redoes it; `pf resume --abandon`
before the database switch drops the candidate and reopens the unchanged deployment instead. A database switch whose
names prove it did not start is redone; one that left the names half renamed stops in `needs_operator`
(`database-switch-unknown`) and is recovered with `rollback <before-reset checkpoint> --restore-db`.

A `reset-db` started on a **schema/image mismatch** can never reopen the unchanged deployment, because its database
is not at the running image's heads. When it is interrupted before the database switch, `pf resume` goes **forward**:
it re-observes the emergency preservation (or captures it again) and completes the reset. `pf resume --abandon` drops
the `pf_clean_*` candidate and closes the operation `cancelled` **without a reopen**: application services stay as the
reset left them (normally stopped), and the message names the routes that remain: a new `pf reset-db` (it preserves
the current data again) or `pf rollback <checkpoint> --restore-db` to a healthy checkpoint. The same applies after
`reset-images-unidentified` on such a reset: its message names `resume --operation <op> --abandon`.

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

Since PF-A3.1 the bundle is a schema-1 `purge-bundle` manifest and also contains:

```text
deployment/deployment-record.json    the current deployment record (when one is sealed and intact)
deployment/compose-resolved.json     the resolved Compose model (sensitive)
```

`images.tar` now also holds the database image layers, saved by image ID (since PF-A3.3 they are used by
`restore-instance` when `postgres:16` is absent, section "Exact instance restore"). Every payload copied from the `before-purge` checkpoint or the deployment record is re-hashed
(`bundle-payload-mismatch` stops the purge before deletion and reopens the application). Retained `pf_keep_*`
databases are dumped with their real heads (connections are allowed only for the dump and closed again);
`postgres-globals.sql` and the role list are evidence only and are never executed. After the bundle is sealed every
store is restored **from the bundle's own payloads** into temporary databases and checked; the resulting
`data_restore_verified` record, bound to this bundle's manifest hash, is required before the first deletion
(`purge-bundle-unverified` otherwise: the purge stops before deletion and the application is reopened). Since
PF-A3.3 that check is replaced by the functional verification of the next subsection. The `pf recoveries` line is:

```text
<n>. <bundle-id>  [<level>]  project=<project>  db=<active database>  source=<git_commit <sha12>|unknown|claimed <sha12>>  derived_from=<checkpoint id>  [legacy-format-N]
```

The recovery bundle is intended to reconstruct **functional PartFlow state**, not Docker
container IDs/network IDs bit-for-bit.

Since PF-A2.3 the bundle files are content-only copies (no mode, owner or ACL is copied) and the bundle gets
the recovery target of the permission policy in force. Purge keeps the permission policy record
(`<root>/instances/<uuid>/permission-policy.json`); the redeployed or restored instance stays under it. The
record is not part of a bundle: an instance registered anew starts with the derived policy.

### Instance purge: isolated final-bundle verification

Since PF-A3.3 the instance purge verifies the **exact final bundle** functionally before it deletes anything. The
application stays stopped (backend and frontend) from the writer stop until the purge ends or is cancelled; nothing
starts them in between.

1. The preview refuses before `PURGE <project>` when a kept isolated topology of this instance still exists
   (`isolated-topology-present`: it runs this instance's images, so their tags cannot be classified; remove it with
   `pf cleanup --apply`, adding `--recovery-target <project>` for a side-by-side target), when the isolated model of
   the current deployment cannot be rendered on this host (`verification-isolation-unsupported`), when the generated
   project name is already used (`topology-name-collision`) or when space is short (`capacity-insufficient`).
2. After the bundle is sealed, the source database is checked with the application's own invariant command
   (`app.cli reconcile` in the running backend image). An image without that command is recorded as `unavailable:`;
   an error or an unreadable report stops the instance purge (`app-check-failed`) and reopens the application.
3. The bundle is restored into a throwaway Compose project `pfverify-<12 hex>`: its own data volume and internal
   network, **no published port** (not even loopback), `restart: "no"`, images by ID from the bundle, and a
   **generated database password** that exists only in `operations/<op>/isolated/<project>/app.env` and
   `compose.json` until the final teardown (a topology that never got a container, for example after a refused
   render-back, loses both files when the purge stops). The bundle's own password is never written into an operation
   file.
4. Checks (all recorded in a `functional_recovery_verified` record): isolation as the daemon reports it (internal
   network, no host port binding, own volume, restart policy), every store (heads, locale, owner, extensions, row
   counts), backend and frontend health, the running image IDs, the image archive proof, the source digest, the
   configuration and the deployment record, and the application invariants: the restored database must give the
   same reconcile result as the source (`clean`/`clean`, or the same mismatch counts); `unavailable` on both sides is
   recorded as `not_run unavailable:`.
5. The topology is torn down by its own frozen deletion plan (only resources carrying its project and topology UUID);
   a teardown that cannot complete keeps the stack, records it as a retained `isolated-topology` and still cancels.
6. The deletion gate (after `ERASE ...`) requires the passed functional record of **this bundle, this manifest hash
   and this operation**, stopped writers and an unchanged source (databases, row counts, heads); otherwise
   `purge-bundle-unverified`, `purge-writers-running` or `purge-source-changed`, and the application is reopened.

A failed check closes the instance purge `cancelled` with `functional-verification-failed` naming the check, reopens
the application and keeps the stopped topology for inspection; `pf cleanup --apply` removes it. Interrupted in
`verifying`, `pf resume` tears the topology down by its plan and reopens the application.

After the deletion, the instance purge marks the registry record `state: purged` through a registry transaction
(owner decision OD-A33-08): the record keeps its UUID and history and releases its Compose project claim, so another
instance may register that project. `pf status` prints `Lifecycle: purged by instance purge <op>` and `instances` shows
`state=purged`. `restore-instance` of this instance's own bundle claims the project again (refused with
`instance-claim-taken` while another instance holds it). A new `deploy` of the purged record does the same (it
re-checks the claim under the registry lock before the first write); `pf abort-deploy` of that incomplete deployment, and
`pf resume --abandon` of a restore that had already registered the record again, return it to `state: purged`. The
state write needs the registry lock; while an installation transaction holds it the operation stops with
`registry-busy` (nothing is changed) and `pf resume` continues it. If that abandon is interrupted after the record is
back at `state: purged` but before the restore is closed, only `pf resume --abandon` continues it (`resume` refuses
with `abandon-in-progress`); it closes the restore `cancelled` without writing the record again. Any other change of
the record still refuses with `plan-authority-changed`.

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

Since PF-A3.2 the purge has a two-stage approval: its plan is written at `PURGE <project>`; the binding deletion plan
and the backup/admin-config choices are frozen in the journal only after `ERASE ...`. A failure or a declined
confirmation before that reopens the application at once and closes the purge `cancelled` (unchanged behaviour). A
purge interrupted before that point is reopened by `pf resume` or `pf resume --abandon` alike: every store the plan
listed as not connectable gets `ALLOW_CONNECTIONS false` again and the unchanged deployment is reopened. The
journal and the instance lock live outside everything purge deletes, so `pf status` shows the purge (and its next
step) even with no `.env` and no daemon. `pf purge` re-enters an open purge in `deleting` or `finalizing` (an alias of
`pf resume`, `RESUME PURGE <project> <bundle-id>`); asking for other backup or admin-config choices than the approved
ones is refused with `plan-inputs-conflict`. A purge removed with `--reset-admin-config` resumes from the admin
configuration it froze. If the recovery bundle no longer reads or verifies, deletion stays blocked (section 16).

### Brand-new redeploy after purge

```sh
sudo pf deploy --latest
```

Because purge removes `config/.env`, a normal full purge causes the deploy wizard to create
a new environment and new PostgreSQL password. If the purge variant preserved an external
configuration for a specific recovery path, the deploy flow validates it before reuse.
`purge --reset-admin-config` also removes `pf-config.json`; the purge then names the next steps:
`sudo pf --instance <slug> config admin`, then `sudo pf --instance <slug> deploy --latest` (with the launcher
of section 5 when `sudo pf` does not reach this root).

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
and health-checks backend/frontend. Since PF-A3.1 the bundle is read strictly before any confirmation, the deployed
source is extracted from the bundle's source payload with the safe importer and compared with its recorded manifest,
and that source (not the saved workspace) is staged as a new deployment; after the health checks a new deployment
record is sealed with `restored_from` naming the bundle, and a fresh `deployed.json` points to it. The bundle's own
`state/deployed.json` stays evidence only. The current installed root-owned control plane is kept;
recovery does not downgrade the lifecycle controller mid-operation. The current
`config/pf-config.json` also remains authoritative. Its saved recovery copy is retained for
manual comparison/reapplication rather than being activated in the middle of a restore.

Since PF-A3.2 `restore-instance` writes a plan with the bundle ID and manifest hash and freezes the bundle's `.env`
before its first effect. `pf resume` (or `pf restore-instance <same bundle>`) continues it forward; another bundle is
refused with `plan-inputs-conflict`. While only the target is being prepared (staging, `.env`, image load, database
start) `pf resume --abandon` (`ABANDON RESTORE <project> <op8>`) removes what this restore created — its frozen
resources, the `.env` it wrote (only with unchanged bytes) and its staging — and gives an edited `.env` back; once the
data restore started only forward is possible. An existing `config/.env` with other bytes is never overwritten: it is
renamed to `config/.env.proposal-<op8>` (`note: env-proposal-preserved`). An existing checkpoint history that differs
from the bundle's is renamed to `backups/revisions/<project>.pre-restore-<op8>` (never deleted, not listed by
`backups`); an identical one is left as it is.

Since PF-A3.3 the restore target is decided by **identity**, before any confirmation:

- a bundle restores only into the instance whose UUID it records; another instance's bundle (including one carried
  over from a lost host) is refused with `restore-target-mismatch` for the exact and the side-by-side restore alike, and
  is not restorable by `pf` in this checkpoint (OD-A33-09: a declared deviation from design r3 LIFECYCLE §9, awaiting
  owner approval). A legacy bundle without a UUID is accepted only for the same Compose project;
- the workspace, repository and home paths recorded in the bundle are **provenance only**: the restore always writes
  the paths of the selected instance and prints `note: bundle-workspace-differs` when they differ; nothing is written
  under the recorded paths;
- **database image**: when the daemon has no `postgres:16` tag, the bundle's database image is loaded from
  `images.tar` (after an archive proof of its config and layer hashes) and tagged `postgres:16`
  (`note: db-image-tagged`). The tag is daemon-wide: the summary lists the other registered instances on the same
  daemon that use it. An existing tag with another image ID is kept (`note: db-image-changed`); a legacy bundle
  without a recorded database image uses the local tag (`note: db-image-unrecorded`);
- after the frontend starts the application invariants of the restored database are compared with the bundle's
  oracle and printed as `Application invariants: <clean|mismatch …|could not run|unavailable>`; the line is evidence
  and does not stop the restore;
- a purged registry record is claimed again (`registry:state=registered` effect) unless another instance holds the
  project (`instance-claim-taken`).

### Side-by-side recovery

If the instance is active but data of one of **its own** bundles is needed for inspection or export:

```sh
sudo pf restore-instance RECOVERY_ID --side-by-side
```

Since PF-A3.3 this creates a kept **recovery target**: a separate Compose project `pfrecover-<12 hex>` with its own
data volume and internal network, **no listener (not even loopback)**, no scheduler and a generated database password.
The exact bundle is restored into it and functionally verified as in the instance purge (the record keeps
`removed: false`). The running instance, its data, listener, image override (`active-images.yaml`), image tags,
workspace and deployment pointer are not changed. When an image of the bundle is no longer on the daemon, the
`image-load` effect only loads `images.tar` (the archive proof and the retag check run again right before the load)
and then requires every image ID of the target; it never writes the image override and verifies no tag against it.
The load may add the bundle's own tags back; it never re-points an existing tag (`image-load-would-retag`). Confirm with `RESTORE COPY <bundle-id>`; it is journaled as a
`restore-side-by-side` operation, `pf resume` continues it, the same command with the same bundle re-enters it, and
`pf resume --abandon` (`ABANDON RECOVERY TARGET <project>`) tears the target down. A target whose data volume or
data checks changed while the operation was interrupted is removed and reported as `recovery-target-lost`. The former
`pf_recovery_*` database mode is gone; an existing `pf_recovery_*` database is only reported (`status`, `cleanup`),
never dropped.

`pf status` lists `Recovery targets: <project> from bundle <id> (operation <op> …)`. Inspect a target as root by its
project label, for example:

```sh
sudo docker ps --filter label=com.docker.compose.project=pfrecover-0123456789ab
sudo docker exec -it <db container> psql -U <user> -d <database>
```

Remove it with `sudo pf cleanup --apply --recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`); a running
target also blocks the instance purge (`isolated-topology-present`).

PartFlow does **not** perform a generic automatic merge between the recovered DB and the
new active DB. `PartMovement`, quantity lineage, allocations, reversals, and derived current
state have domain invariants that cannot be safely reconciled by generic SQL insertion.
Recover old data side-by-side, then build an explicit domain-aware import/reconciliation
procedure for any data that truly must be carried forward.

### Aborting a first deployment after frontend access opened

Since PF-A3.3 `pf abort-deploy` is also allowed after the incomplete first deployment opened frontend access (and
before its deployment pointer was written). Users may have written data, so the abort **preserves the current
database first**: it stops backend and frontend, captures a `before-abort` checkpoint (emergency preservation when the
contract does not hold) under `backups/revisions/<project>/`, and only then deletes the frozen resources of the
incomplete deployment. The checkpoint survives the abort. The database must be reachable, otherwise the abort is
refused before the confirmation (`preservation-failed`); a capture failure leaves the abort open in `preserving` with
nothing deleted (`pf resume` retries the preservation, `pf resume --abandon` closes it and the deploy blocks again).
The confirmation summary is kept as `confirmation-summary.txt`. Before the frontend opened the abort is unchanged.

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

Once grants exist, a root-owned DSM task names its instance and runs the wrapper that
`install-control.sh init` places in `<root>/bootstrap/` next to the installed launcher (PF-A2.1), or the
launcher itself; until then the placed wrappers are refused like any other unattended command:

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

Since PF-A3.1 the `backup` output gains the `Checkpoint class:` and `Verification level:` lines (section 10), and a
scheduled `backup`, once granted, refuses `deployment-image-mismatch` exactly like a manual one. `backup --emergency`
is never scheduled: it needs a terminal and `backup.sh` does not pass it.

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

Since PF-A2.2 `pf config` is the configuration wizard group (`pf config admin`, `pf config app`).
`pf config` alone or with any other word (for example the former `pf config --services`) is still refused
with `compose-route-removed`; the raw Compose model is not available (`pf doctor` validates it privately).

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

**PF-A3.2:** do not run the raw form while `pf status` shows an open operation — in particular in
`syncing-workspace` or `workspace_sync_pending`, where the workspace path passed as `--project-directory` and
`PARTFLOW_REPO_ROOT` may be absent — and never with an edited `config/.env` that an open operation has not frozen (the
controller uses each operation's frozen `app.env`).

Never use broad cleanup commands such as:

```text
docker system prune --volumes
docker volume prune
```

as a PartFlow reset mechanism on a NAS that may host other workloads.

## 15. Updating the control plane

Application `update` intentionally does not update the control plane. Install a reviewed control
release explicitly:

```sh
sudo <root>/bootstrap/pf install control --source <reviewed repository tree>
```

- The summary shows the bound and the new release (checkpoint, file count, inventory hash), every
  instance that is rebound (one control release per root), the default (unchanged) and whether the
  application needs a separate operation.
- After `INSTALL CONTROL <release-id>` the release is staged privately and smoke-checked with the
  registered interpreter: the new code must import, load the registry and every record, and accept the
  live `pf-config.json` and `.env` of every instance that the running release accepts. It is verified
  again, published, bound (`bootstrap.conf`, then every record) and verified end to end through the real
  bootstrap. If that verification fails the previous binding is restored automatically
  (`install-verify-failed`, operation `rolled_back`); the new release stays retained and inert.
- `app_operation_required: update` means the new release changes `compose.nas.yaml`: the running
  application keeps its containers, and the next `pf --instance <slug> update` applies the new topology.
- Go back to (or forward to) a retained release with
  `sudo <root>/bootstrap/pf install control --release r-<16 hex>` (`SELECT CONTROL <id>`), which runs the
  same smoke, contract and bootstrap checks. A source tree whose release is already published is selected
  the same way.
- An interrupted operation is continued with `pf install resume` or undone with
  `pf install resume --abandon`; an interrupted rollback always finishes the restore.
- `--source` always names a source tree and `--release` a retained release id; a value of the wrong kind
  is refused, never re-interpreted. Only an answer typed at the `candidate` prompt is classified (a
  leading `/` is a source tree).
- A candidate whose only change is a scheduler wrapper (`backup.sh`, `release-check.sh`) is
  `control-unchanged` (exit 0): the message names the differing wrapper. A wrapper-only change is not
  installed on its own; it is installed together with the next control release whose files change.
- An interrupt during the automatic restore after a failed verification is `install-interrupted`: the
  operation stays `rolling_back` and every instance route is refused until `pf install resume` finishes
  the restore.
- The launcher and verifier bytes are frozen: a candidate that changes `pf.sh` or `pf_bootstrap.py` is
  refused with `bootstrap-change-unsupported` (a launcher migration is PF-A4.3).
- Installing or selecting a release never migrates `pf-config.json`. Once an instance's file is schema 2,
  a retained release older than PF-A2.2 rejects it in the smoke check (`install-smoke-failed`, nothing
  bound); restore the schema 1 file first if you really need to go back.
- The legacy `recovery/control-upgrades/` archive no longer applies; old releases stay under
  `<root>/releases/`.
- **PF-A2.2 → PF-A2.3.** The bootstrap bytes and the install contract are unchanged, so `pf install control
  --source` is legal. Without an approval the derived policy keeps the A2.2 groups and modes, except that
  backups and recovery keep the group already on their folders, `deploy --current` no longer changes the
  workspace, fresh copies are content-only and bare `pf permissions` is refused. **Downgrade** after an
  approval: an older control ignores `permission-policy.json` and goes back to the `pf-config.json` groups.
  The installer refuses any control switch, upgrade or downgrade, while a permission apply is open
  (`instance-operation-pending`): finish it with `--resume` or `--abandon` first.

- **PF-A3.1 → PF-A3.2.** The bootstrap bytes are unchanged. `pf install control` is refused while any instance
  operation is open (`instance-operation-pending`, naming the operation; also for an invalid journal or an operation
  index overflow). Runner records (`unresolved-effects.json`) of a journaled operation are reconciled by its closed
  journal (`status --operation` shows `reconciled by journal sequence <n>`), and those of a superseded operation by
  the closing journal of the last recovery of its supersession chain; records in an operation directory without a
  journal still refuse with `instance-effects-unresolved`. Such directories come from a `backup --emergency`, a
  side-by-side restore, a pre-A3.2 operation, and from any lifecycle command whose child process was
  interrupted or timed out before the confirmation, when no plan exists yet (for example Ctrl-C during the candidate
  `compose build`, the `compose run` contract checks of `deploy`, `update` and `rollback`, or
  `ensure_local_contract`): inspect them with `pf status`, confirm that nothing of them still runs, and keep the
  directory. Since PF-A3.3 acknowledge them with `sudo pf resume --operation <op> --acknowledge`
  (`ACKNOWLEDGE <op8>`): pf first refuses while an owned one-off container still runs, or, when a record names a
  database effect, while a client session is open on a database the records name (every client session when such a
  record names no database; then stop the application before acknowledging) (`effect-still-running`). The running
  application's own sessions on its database never block a record without a database effect. pf then prints what it
  observes (identities only), and writes
  `operations/<op>/acknowledgement-<hash12>.json` (0600, exclusive create) bound to the hash of the current records. The
  records then no longer block `pf install`; `pf status` counts them as acknowledged. A record appended later is open
  again. A journaled operation, a directory without records or one already acknowledged is refused with
  `acknowledge-not-legal`.
- **PF-A3.2 → PF-A3.3.** The bootstrap bytes are unchanged; `pf install control` is refused while any instance
  operation is open. **Downgrade** to the PF-A3.2 control only with no open operation (including no open
  `restore-side-by-side` or `cleanup`): the older control does not know the new operation kinds, recovery targets or
  acknowledgement files (acknowledged records count as unresolved again there), and it does not write the `purged`
  registry state itself (a record already marked `purged` stays so).

This explicit step is the security boundary that permits `repo/` to remain users-writable.
Do not install an unreviewed or unknown tree with `sudo`.

## 16. Troubleshooting

### SMB can see `repo/` but cannot edit

Compare first, then apply the permission policy (start from outside the folders, for example `cd /`):

```sh
sudo pf --instance <slug> permissions check --scope workspace
sudo pf --instance <slug> permissions apply --scope workspace
```

Then verify DSM Shared Folder permissions grant the account/group Read/Write access from the share root.

### `pf permissions` codes

- `permissions-verb-required` (exit 2): bare `pf permissions` no longer changes anything; use `check`, `plan` or
  `apply`.
- `permission-policy-invalid`: the policy (or the derived one, for example a group name with `/`) breaks a rule;
  nothing was changed.
- `permission-group-missing`: a group of the policy, or the gid on the backups/recovery folder, has no group on
  this host. Groups are never created; choose an existing group in `pf permissions apply`.
- `permission-policy-unsupported`: a valid choice this control does not activate (workspace script execution
  "none").
- `permission-approval-invalid`: `permission-policy.json` is not a root-owned `0600` single-link file or fails its
  schema or chain. It is never replaced automatically and no derived policy is used: backups and the permission
  commands stop. Manual route: as root, move it aside in the same directory under a name such as
  `permission-policy.invalid-<UTC>.json`; the instance then uses the derived policy (backups keep the group of
  their folders, so nothing widens), and `pf permissions apply` writes a new revision 1.
- `permissions-context-refused`: a protected-context finding outside the scopes, or one on a folder above the
  installation root (even when it is above a scope too); `check`/`plan` read no scope.
  Fix the findings shown (`pf doctor`).
- `scope-path-unsafe`, `scope-entry-link`, `scope-entry-special`, `scope-entry-hardlinked`,
  `scope-mount-boundary`, `scope-contains-app-storage`, `scope-untrusted-owner`, `scope-entry-acl`,
  `scope-too-large`: blockers; `apply` changes nothing while one exists. Remove the link, special file, hard
  link or ACL, move the mount or application storage out of the folder, or deselect the scope with `--scope`.
- `control-ceiling`: the installed control release breaks its ceiling; it is changed only by
  `pf install control`.
- `editor-freeze-unavailable` / `editor-freeze-refused`: see section 2 (Editor freeze).
- `permissions-blocked`, `permissions-cancelled`, `permissions-current`, `permissions-changed-before-apply`:
  nothing was changed; run the command again to review.
- `permissions-apply-pending`: an interrupted apply is open; every other mutating command (scheduled `backup`
  included) is refused until `pf permissions apply --resume` or `--abandon`.
- `permissions-entry-changed`, `permissions-verify-failed`, `permissions-interrupted`: the apply is **not
  complete**; `--resume` re-plans the remaining changes with the frozen policy, `--abandon` compensates the
  recorded changes in reverse order.
- `permissions-already-approved`: the interrupted apply already wrote its revision; only `--resume` can finish
  it.
- `permissions-abandon-conflicts` (exit 1): objects changed after the interruption are left as they are and
  listed.
- `permissions-journal-invalid`: an effect journal line cannot be read; nothing was changed and the journal
  stays open. Review `<root>/instances/<uuid>/operations/<op-id>/permission-effects.jsonl` by hand.
- `fresh-entry-acl`: a new backup or recovery entry inherited an ACL from its folder; the operation stopped
  before publishing it. Remove the default ACL from that folder. In the workspace or the configuration the new
  entry keeps the mode it was created with (a note).
- `permissions-nothing-pending`: `apply --resume` or `--abandon` found no interrupted apply; nothing was changed.
  Run `pf permissions check` to see the current state.
- `permissions-option-invalid` (exit 2): `--resume` and `--abandon` act on the frozen plan of the interrupted
  apply, so `--scope` cannot be combined with them. Run the command again without `--scope`.
- `permission-policy-unapproved` (note): no policy is approved yet; the derived policy is in force (backups and
  recovery groups come from their folders, workspace and configuration groups from `pf-config.json`). Run
  `pf permissions apply` to approve one.
- `permission-entry-unplanned` (note): an entry appeared after the plan was shown (in a fenced scope: after its
  authoritative inventory, which already gave the earlier new entries their target) and was left as it is. Run
  `pf permissions check` and apply again if it differs.
- `permission-entry-gone` (note): an entry of the plan disappeared before it was verified; nothing else is needed.
- `workspace-concurrent-entry` (note): an editor created an entry in the workspace while an operation (for
  example `deploy` or `update`) published new workspace entries; it was left untouched. Review it, then run
  `pf permissions check --scope workspace`.
- `workspace-no-source-manifest` (note): no protected source manifest exists, so no workspace file is designated
  executable. Deploy a source (`pf deploy`/`pf update`) to record one.
- `permissions-applied`: the apply completed and verified every selected scope; the line names the policy
  revision in force.

### SMB cannot modify backups/recovery

That is intentional. These are recovery artifacts and remain group read-only. Copy them
elsewhere if an editable copy is needed.

### `sudo sh ./pf.sh ...` refuses to run

Expected. The repo copy is source only. Run the installed launcher:

```sh
sudo <root>/bootstrap/pf ...
```

or create a new installation root first (section 5 (a)):

```sh
sudo sh ./deploy/synology/install-control.sh init --root <root>
```

### `install-preflight-refused`

The preflight lists every conflict at once, each as `code: subject: detail`, and nothing was changed.
Resolve every item and run the same command again. Frequent codes: `root-exists`, `init-leftover-unknown`,
`source-*`, `install-contract-incompatible`, `release-not-retained`, `release-id-collision`,
`interpreter-*`, `free-space`, `registry-*`, `slug-*`, `project-*`, `daemon-*`, `registered-path-missing`,
`path-*`, `storage-replaceable`, `acl-*`, `admin-config-*`, `group-missing`, `legacy-*`,
`instance-operation-pending`, `instance-effects-unresolved`, `launcher-parent-untrusted`.
`legacy-env-conflict`/`legacy-admin-config-conflict`: neither copy is chosen automatically; keep the
correct one in `config/`, move the other out of both locations.

### `install-operation-pending`, `legacy-control-active` or `control-binding-changed`

`install-operation-pending`: an install operation is open (interrupted, crashed or `needs_operator`).
Inspect it with `sudo <root>/bootstrap/pf install status` and follow its next step; until it is terminal an
open `control` or `init` refuses every instance's mutating route, and an open `register`/`migrate-legacy`
refuses its own instance. `legacy-control-active`: the instance was migrated from v2.5 and its v2.5
`control/` still exists; keep using v2.5 for changes until legacy adoption is installed. `control-binding-
changed`: a control installation completed while the command was starting; nothing was changed, run it
again.

### `install-needs-operator`, `bootstrap-change-unsupported` or `install-busy`

`install-needs-operator`: resume found a target in a state the installer did not write; the message names
the target, the expected hashes and what it found, and the journal records the evidence (for `.env` it
names the planned copy and what was found, never the file's hash). Restore the target, then
`pf install resume` (or `pf install resume --abandon` where offered). A global launcher that another
writer created meanwhile is never a reason: resume leaves it in place with the note `launcher-left`.
`bootstrap-change-unsupported`: the candidate changes the launcher or verifier; reinstall into a new root
or keep those bytes. `install-busy`: another operation holds a needed lock (registry, instance, v2.5 or
an init build directory); nothing was changed, retry after it finishes.

### `install-smoke-failed`, `install-verify-failed` or `abandon-not-possible`

`install-smoke-failed`: the candidate did not pass its smoke check (for example it rejects an instance's
live configuration); it was not activated and its staging was removed. `install-verify-failed`: for
`control` the previous binding was restored (`rolled_back`); for other kinds the operation stays in
`needs_operator` with its next step. `abandon-not-possible`: an `init` that already published its root, or
a registration that has been used (default set, operations recorded, record changed, lock held), cannot be
abandoned; finish it with `pf install resume`.

### `install-operation-unreadable` or `install-interrupted`

`install-operation-unreadable`: an entry of `<root>/install-operations/` is not a readable install
operation (for example a stray directory). It refuses every route, fail closed, and `resume` cannot
continue it. Inspect it as root and move it out of `install-operations/` (keep a copy: an operation
journal is evidence), then run the command again. `install status` and the gate message name the same
step. `install-interrupted`: the operation stopped after its commit point or during the automatic
restore; it stays open in the phase the message names. Run `pf install resume`. When the message says
the operation is cancelled but its cleanup was interrupted, nothing else was changed: what remains stays
in its operation directory or is recognized by the next run of the same command.

### `pf config` refusals and notes

Every refusal below changed nothing; the message names the next command.

- `config-option-invalid` (exit 2): `--configuration [--project]` is the pre-registration form of
  `config admin` only; it cannot be combined with `--instance` or used with `config app`, and `--project`
  needs `--configuration`.
- `config-cancelled`: `q`, end of input, Ctrl-C or any answer but `y`/`yes` at `Write …? [y/N]`. A generated
  password was discarded.
- `config-current` (exit 0): nothing to change. Notes after it still apply.
- `admin-config-required`: `config app` needs a valid `pf-config.json`; run `config admin` first.
- `admin-config-invalid`: the file has a parse, key, type or rule problem (the first one is shown, with the
  count of the others). Fix it by hand; the wizard never repairs a file. Before registration the copy repeats
  `--configuration <dir>` (and `--project`) so the next run re-checks the same directory.
- `admin-config-version-unsupported`: the file declares another `schema_version`. A newer control wrote it:
  select that control again (`pf install control --release <id>`) or restore the previous file.
- `admin-config-migration-blocked`: an explicit schema 1 value is not valid in schema 2; correct it by hand.
- `admin-config-mismatch`: `project` differs from the registration, or `environment` from the approved policy.
  Restore the registered value; a policy change is a separate approval.
- `admin-config-legacy-unregistered` (exit 0): a schema 1 file in a directory that is not registered was left
  as it is; register first, then run `config admin` with `--instance`.
- `admin-example-invalid`, `app-example-invalid`: the installed example is not usable; run `doctor` and
  reinstall the control release.
- `app-config-invalid`: `.env` does not parse (unknown or duplicate key, `export`, quoting, line endings).
  The message names the line; it never shows a character of the value (an unsupported escape, invalid UTF-8
  or a control character is described, not printed).
- `app-credential-unusable`: the instance counts as deployed and a credential is missing or invalid; restore
  it from the recovery bundle or your records.
- `app-profile-undeclared`: the instance's profile declares no application variables in this control.
- `migration-issue`: an existing value cannot be rendered literally (single quote, trailing backslash, control
  character) or the password is shorter than 4 characters; fix the file by hand.
- `zone-data-unavailable`: no host zone data to verify a new `SITE_TIMEZONE`; install it or write the value by
  hand. As a note: an existing value was kept.
- `config-file-acl`: the file carries ACL entries; apply the listed changes by hand (PF-A2.3).
- `config-file-unsafe`: the file is a link, a special or hard-linked file, or the directory holds a reserved
  temporary name that is not a leftover of an interrupted run; inspect it, nothing was removed.
- `config-changed`: the file changed while the wizard ran; run the command again to review the new file.
- `config-interrupted` (exit 1): an interrupt (Ctrl-C, `SIGTERM`, `SIGHUP` after an SSH drop) arrived while
  the file was being published, and the file already holds the new content. Nothing else was changed and no
  change record was written; run the command again to review the file (it reports `config-current`). An
  interrupt before the publish is `config-cancelled` and leaves the file unchanged.
- `config-audit-not-recorded` (exit 1): the file was written (the message says whether it still holds the new
  content), but recording `config-change.json` failed or was interrupted. The change itself is complete;
  run the command again to review the file.
- `config-busy`: another registration holds the registry lock; try again.
- `config-dir-invalid`, `config-path-conflict`: the pre-registration directory is missing, non-canonical, in
  replaceable ancestors, or used by a registered instance (then use `--instance <slug> config admin`).
- `registry-record-invalid`, `registry-invalid` (pre-registration): a registered instance record (or the
  registry) cannot be loaded, so the wizard cannot tell whether the directory belongs to it; nothing is asked
  or created. Repair the record first (as for `pf install register`).
- `config-audit-invalid`: internal error; the change record failed its schema and nothing was written.
- Notes: `config-temp-removed` (leftover temporary files of an interrupted run were removed),
  `zone-unknown-on-host`, `password-weak-for-new-deployment`, `implicit-materialized`.

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

`backup` or `release-check` ran without a terminal and the instance was selected
by the protected default or as the only registration. An unattended command must name its
instance: `pf --instance <slug|uuid> <command>`. Nothing was changed.

### `policy-grant-required` or `auto-apply-not-permitted` (exit 20)

An unattended `backup` or `release-check`, or any `release-check --apply`, needs a
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
`observed-tags.json`, its state files are not a list, or it lists a state file without its verified
`state/<name>` payload (a legacy bundle then reports `manifest-schema-unsupported`). Nothing was changed.

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

### Lifecycle bundle and deployment record codes (PF-A3.1)

A refusal before any effect ends with `Nothing was changed.`; a later one names the actual state.

- `manifest-checksum-mismatch`, `manifest-invalid`, `manifest-schema-unsupported`, `bundle-payload-mismatch`,
  `bundle-unlisted-file`: the bundle folder was changed, damaged or written by an unsupported control. Do not edit
  or "repair" it; copy it off-NAS as evidence and use another checkpoint or recovery bundle. `pf backups` and
  `pf recoveries` list such a folder as `[invalid: <code>]` (a folder with more than 20000 entries is
  `[invalid: bundle-unlisted-file]`). A bundle copied back from off-NAS must be byte-identical (no added files such as
  `.DS_Store` or `Thumbs.db`).
- `archive-member-refused`, `archive-unreadable`, `archive-changed`, `archive-capacity`: an archive payload contains a
  link, special file, unsafe or duplicate path, exceeds the importer limits (a PAX or GNU long-name header above
  64 KiB is refused before it is read), changed while it was read, or does not fit; nothing was extracted. For
  `archive-capacity` free space on the volume and retry.
- `workspace-archive-limit`: the editable workspace differs from the deployed source and holds a file over 128 MiB,
  more than 512 MiB or 200000 files in total, or a path the archive cannot hold. `pf backup`, `update`, `reset-db`,
  `purge` and `rollback` refuse in their preflight, before any confirmation or pause (the application keeps running):
  their capture never proceeds without the drifted workspace, which a later source replacement would destroy. Move
  the files out of the repository workspace and retry; `pf --instance <slug> backup --emergency` preserves the database meanwhile and records the
  workspace as excluded.
- `source-manifest-mismatch`: the extracted source differs from the recorded source manifest; use another
  checkpoint.
- `checkpoint-not-rollback-target`: the selected checkpoint is an emergency or partial capture. Restore its data
  manually or export from it; choose a healthy checkpoint for `rollback`.
- `checkpoint-incompatible`: the `--restore-db` candidate failed a check (locale, extension, owner, heads or row
  counts); the candidate was dropped and the current database is unchanged.
- `preservation-failed`: the rollback could not capture and restore-test the current database; nothing was restored
  or switched and the services stay stopped. Preserve the database manually — a `pg_dump --format=custom` of the
  named database from the `db` service to a protected, root-only location — or fix the cause and run
  `pf --instance <slug> backup --emergency`; then retry, or run `pf --instance <slug> resume` to reopen the unchanged
  deployment. Once a rollback got past that point (`resume` is no longer a legal route), roll back again to a healthy
  checkpoint with `--restore-db`: the preservation capture its journal records may be an emergency or partial
  capture, which is evidence and data, never a rollback target.
- `deployment-artifact-mismatch`: a file of the current deployment record differs from the record. **Do not delete
  the folder**; keep it as evidence. As a note the capture continued with the source from the protected source store
  or the proven workspace and the next `update` seals a fresh record. As a refusal the deployed source is
  unprovable: restore the protected source store, or roll back to a healthy checkpoint (the rollback preserves the
  current data first).
- `deployment-image-mismatch`: the running backend/frontend image is not the current deployment's, so a healthy
  checkpoint would bind the wrong images. Find out who changed the containers, preserve the data with
  `pf backup --emergency` if needed, and redeploy or roll back. After a pause the copy names the stopped state and
  `pf resume` reopens the unchanged deployment.
- `deployment-stage-failed`: the deployment could not be staged; the application, database and workspace were not
  changed. Fix the detail (often space or permissions in `artifacts/deployments`) and rerun.
- `deployment-record-incomplete`: the application was activated and is healthy, but its record could not be sealed.
  `status` shows `Deployment: not recorded (…)`; captures use the protected store or workspace proof meanwhile, and
  when no commit is provable healthy checkpoints and `update` refuse in their preflight — use `rollback` (it falls
  back to emergency preservation) or `backup --emergency`. The next deploy, update, rollback or restore-instance seals
  a record.
- `artifact-capacity`: the deployment artifact store needs the source size plus 64 MiB; free space and retry.
  Nothing in PF-A3.1 deletes deployment artifacts (retention is PF-A5.1).
- `purge-bundle-unverified`: the purge bundle has no passed `data_restore_verified` record for its manifest; deletion
  is blocked and the application is reopened. Rerun `purge` after fixing the restore failure shown before it.
- Notes: `verification-record-invalid` (a damaged verification record is ignored; the level falls back),
  `legacy-manifest-migrated` (an older bundle was read through the migration), `db-image-changed` (a different
  PostgreSQL image of the same major), `verification-failed` (code-only rollback of a checkpoint whose last restore
  test failed; the dump is not used).

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
sudo pf --instance <slug> status
sudo pf --instance <slug> status --operation <operation-id>
```

`status` prints, before any live check, one block per open operation:

```text
Operations: <op> <kind> phase <phase> sequence <n> updated <stamp> [blocking|needs operator]
  last error: <code>: <message>
  unresolved effect: <eid> <type> <target> (<state>)
  next (recorded at sequence <n>): <command>: <description>; ...
```

followed by `Recent operations:`, `Workspace generations:` and `Runner effects:` when present. `status --operation`
lists every effect with its state and evidence, the attempts and the recorded child processes (no secret, no frozen
value). Use only the printed next steps. **Never delete or edit a journal**: an operation directory is evidence and
its plan is the approval.

Codes:

- `operation-open` — another command than a legal next step for the open operation; the message lists them.
- `operation-needs-operator` / `effect-unknown` / `database-switch-unknown` — no automatic continuation is safe;
  use the printed recovery route (`rollback <checkpoint> --restore-db` for an update, rollback or reset, `abort-deploy`
  for a first deployment, `backup --emergency` to preserve data first).
- `effect-still-running` — a recorded child process group, an owned one-off container or a database session still
  runs; `resume` never stops it. Wait, then run `resume` again. For `resume --operation <op> --acknowledge` only owned
  one-off containers and the sessions on the databases its records name count (section 15). `effect-probe-unavailable` — processes were recorded
  but `/proc` or the boot ID cannot be read here; nothing is decided without that probe.
- `database-unavailable` — `resume` could not start the database service to observe an effect; the operation is
  unchanged.
- `plan-authority-changed` — the instance record, policy, control release, profile or daemon changed after the
  approval; restore the approved state or have an administrator review it. `plan-input-changed` — a frozen input (a
  bundle, a checkpoint, the frozen `app.env` or `admin-config.json`, the staged deployment) changed; restore it
  byte-identically. `plan-inputs-conflict` — an alias asked for other choices than the approved ones.
- `abandon-not-legal` (a live effect already started), `abandon-in-progress` (only `resume --abandon` continues an
  accepted restore abandon), `keep-workspace-not-legal` (the new workspace is already bound). Where `--abandon` is
  legal before a data effect it does what `resume` does there and then cancels: owned candidate databases are
  dropped, a purge's non-connectable stores are closed again and the unchanged deployment is reopened (a superseding
  rollback withdraws instead).
- Refusals found while observing (`workspace-generation-mismatch`, `checkpoint-history-unknown`,
  `keep-workspace-not-legal`, `plan-input-changed`) are decided before the typed confirmation: no attempt or approval
  is recorded and the operation files are unchanged.
- `workspace-generation-mismatch` — `repo/`, the retained generation or the stage is not what the journal recorded;
  nothing was moved. Restore the recorded state (for example remove a folder created at `repo/` meanwhile) and run
  `resume`, or `resume --keep-workspace`. `workspace-validation-failed` — the bound workspace does not validate; fix
  the finding and run `resume`. `checkpoint-history-unknown` — move the foreign tree aside as root, then `resume`.
- `journal-changed` — another writer changed the journal; the process stopped. Run `status`.
- `operation-conflict` — more than one operation is open (not the allowed pair of a backup next to an update waiting
  in `workspace_sync_pending`); every mutating route is refused until an administrator reviews them with `status
  --operation`. For the pair, `resume` without `--operation` names the backup: finish or abandon it with `resume
  --operation <backup-op> [--abandon]` first; the waiting operation stays untouched until then (section 8).
- `operation-journal-invalid` / `journal-format-unsupported` — a journal or plan does not validate, or a
  `state/pending.json` other than a permission apply exists (only an unsupported control change writes one). Every
  mutating route is refused; `status`, `doctor`, `backups`, `recoveries`, `ps` and `logs` work. Keep the directory,
  compare it with `status --operation`, and contact the control owner before moving anything.
- `operation-index-overflow` — more than 20000 entries in `operations/`. Archive **closed** operations
  (`completed`, `cancelled`, `failed_preserved`, as shown by `status --operation`) as root to a folder outside
  `operations/`, never an open or superseded one; retention is PF-A5.1.
- A purge in `deleting` whose recovery bundle no longer reads or verifies (`plan-input-changed: recovery bundle ...
  no longer reads or verifies`) keeps deletion blocked: restore the bundle folder byte-identically from an off-NAS
  copy, then run `resume`.
- In the workspace-switch interval only `resume` and `resume --keep-workspace` run (section 8).

### Integrated operation codes (PF-A3.3)

- `isolated-topology-present` — a kept `pfverify-*` stack or a `pfrecover-*` recovery target of this instance exists;
  run `pf cleanup --apply` (add `--recovery-target <project>` for a recovery target), then the instance purge again.
- `verification-isolation-unsupported` — the isolated model cannot be rendered or validated on this host (or a legacy
  bundle has no usable image); nothing was deleted. `topology-name-collision` — run the command again.
- `functional-verification-failed` — a check of the isolated verification failed (the message names it); the
  instance purge was cancelled and the application reopened, or the side-by-side restore closed `failed_preserved`.
  The stopped topology is kept for inspection; remove it with `pf cleanup --apply`.
- `app-check-failed` — the application invariant command failed or gave an unreadable report on the source database;
  nothing was deleted. Its output is never stored or printed (only counts per check). The command runs within the
  runner's ten-minute child limit; a database where it takes longer stops the instance purge the same way.
- `purge-bundle-unverified`, `purge-writers-running`, `purge-source-changed` — the deletion gate refused after
  `ERASE`; the application was reopened (or, when the reopen itself cannot proceed, the purge stays open for
  `resume`/`--abandon`).
- `restore-target-mismatch` — the bundle belongs to another instance (section 12). `instance-claim-taken` — another
  registered instance holds the project of this purged record. `registry-busy` — an installation transaction holds the
  registry lock; run `resume` after it finishes.
- `image-load-would-retag` — loading the bundle's `images.tar` would move a tag that names another image here;
  nothing was loaded. Remove or rename that tag first.
- `recovery-target-lost` — a side-by-side target changed while its operation was interrupted; it was removed. Run the
  side-by-side restore again.
- `reset-contract-unknown`, `reset-deployment-image-mismatch`, `preservation-failed` — section 11 and "Aborting a
  first deployment after frontend access opened".
- `effect-still-running`, `acknowledge-not-legal` — section 15.

### Capacity refusals

`capacity-insufficient: <phase> needs <n> MiB on device <d> (<roles>: <paths>), <m> MiB free including the <f> MiB
safety floor` names every role that shares the short device. Free space on that device (an old `.pre-restore-*`
history, exported files, or `pf cleanup --apply` leftovers); never delete the last healthy checkpoint or a purge
bundle to make room. The Docker root is shared by every instance on the daemon, so another instance's images and
volumes count there. The instance purge's recovery-device need includes the image archive of the final bundle (the
`docker image inspect` sizes of the saved tags and the database image); an image without a size refuses with
`capacity-unmeasurable`. `capacity-unmeasurable` means the daemon reported no usable `DockerRootDir` or it cannot be
measured; `pf backup --emergency` still preserves the database. Lowering `minimum_free_mb` is an admin
configuration change, not a recommended route.

### Cleaning up leftovers

`sudo pf cleanup` is read-only (observe-only lock, no operation directory): it lists the leftovers that **closed**
operations recorded: `pf_verify_*`, `pf_migrate_*`, `pf_restore_*` and `pf_clean_*` candidates, kept `pfverify-*`
topologies, bundle-attempt folders and their image tags, superseded stages, and the selector-only items. A name that
only matches a prefix is never authority: a database no operation recorded is not listed and never touched.
`sudo pf cleanup --apply` removes the default set after `CLEANUP <project> <op8>` as a journaled `cleanup`
operation; every item is re-observed right before its removal and kept when it changed (`cleanup-item-changed`) or is
busy (`cleanup-item-busy`); the cleanup then continues and closes `failed_preserved` (`cleanup-items-kept`).
Selector-only items need their selector and phrase:

- `--recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`);
- `--checkpoint-history <name>` (`DELETE CHECKPOINT HISTORY <name>`): a displaced `.pre-restore-*` history, only when
  every checkpoint in it is also in the active history with the same manifest and that active copy reads strictly
  (payload hashes), and the history holds nothing else (`cleanup-history-unique-checkpoint` otherwise, naming the
  checkpoint or the other entry; the folder is removed as a whole);
- `--generation <wsg-…>`: a retained workspace generation (next subsection).

Never cleaned: `pf_keep_*` retained databases, `pf_recovery_*` databases of the former side-by-side mode, the live
database, names of open operations, sealed checkpoints, purge bundles, sealed deployments, `unsealed-active-staging`
and image tags loaded by an abandoned restore. `cleanup-nothing` and `cleanup-target-unknown` change nothing.

### Retiring a retained workspace generation

`sudo pf cleanup --apply --generation wsg-<…>` seals the generation before removing it: close every editor or SMB
session that has files open in it first (an open handle refuses with `generation-in-use`; unreadable `/proc` with
`generation-handles-unverifiable`). The tree is archived to `backups/generations/<project>/<generation>/workspace.tar.gz`
with `seal.json` (hashes of the tree and the archive); a link or special file inside refuses with
`generation-unsupported-entry`, a tree that changes while it is sealed with `generation-unstable`, and a write after
the seal keeps the tree (`cleanup-item-changed`). A valid seal of an earlier cleanup is never replaced: when the tree
still has the sealed content it is reused and the tree removed; when the tree changed since (for example after an
interrupted removal) both are kept with `generation-seal-exists` — the earlier archive may be the only complete copy.
Compare the two and remove the generation folder by hand only once nothing of it is needed. To get a file back, extract it from the archive as root to a
location outside `repo/`, for example `sudo tar -xzf <archive> -C /tmp/restore-<generation> <path>`.

## 17. Command reference

| Command | Purpose |
| --- | --- |
| `sudo pf doctor` | Validate host tools, control security, Compose config, env, and basic capacity |
| `sudo pf permissions check [--scope S]…` | Compare every scope with the permission policy in force (read-only; exit 1 on a difference) |
| `sudo pf permissions plan [--scope S]… [--details]` | Preview groups, members, counts and the plan hash (read-only) |
| `sudo pf permissions apply [--scope S]… [--details]` | Wizard, `APPLY PERMISSIONS <slug>`, fenced and verified apply; writes the next permission policy revision |
| `sudo pf permissions apply --resume \| --abandon` | Finish (`RESUME PERMISSIONS <slug>`) or compensate (`ABANDON PERMISSIONS <slug>`) an interrupted apply |
| `sudo pf status` | Show deployed revision, workspace drift, DB revision, containers, pending operation |
| `sudo pf deploy --latest` | Brand-new staging deployment from latest configured branch SHA |
| `sudo pf deploy --commit FULL_SHA` | Brand-new deployment from an explicit commit |
| `sudo pf deploy --release TAG` | Brand-new deployment from a published release |
| `sudo pf abort-deploy` | Remove an incomplete first deploy (confirm `ABORT DEPLOY <project>`; after frontend access opened the current database is preserved first; an interrupted abort resumes with `RESUME ABORT DEPLOY <project>`) |
| `sudo pf update --latest` | Managed staging update from latest branch SHA |
| `sudo pf update --commit FULL_SHA` | Managed staging update to exact commit |
| `sudo pf update --release TAG` | Managed staging update to release |
| `sudo pf backup` | Create and restore-test a healthy revision checkpoint (prints class and level) |
| `sudo pf backup --emergency` | Preserve the actual data when a healthy checkpoint is refused (`EMERGENCY BACKUP <project>`; terminal only; never a rollback target) |
| `sudo pf backups --page N` | List revision checkpoints, 10/page: `[class\|level]`, reason, DB heads, source provenance, legacy format |
| `sudo pf rollback [BACKUP_ID]` | Code rollback with current DB retained |
| `sudo pf rollback BACKUP_ID --restore-db` | Restore code + selected database state |
| `sudo pf reset-db` | Activate a clean migrated DB while preserving recoverability |
| `sudo pf instances` | List managed PartFlow instances |
| `sudo pf purge [--project NAME]` | Full recoverable purge of one staging instance |
| `sudo pf recoveries` | List purge recovery bundles: `[level]`, project, active database, source provenance, `derived_from` |
| `sudo pf restore-instance RECOVERY_ID` | Recreate a purged functional instance |
| `sudo pf restore-instance RECOVERY_ID --side-by-side` | Restore one of the instance's own bundles into a kept, isolated `pfrecover-*` recovery target beside it (`RESTORE COPY <bundle-id>`) |
| `sudo pf release-check` | Check eligible release without applying |
| `sudo pf release-check --apply` | Refused in this checkpoint (exit 20): needs a protected policy grant (PF-A4.3) |
| `sudo pf resume [--operation ID]` | Re-enter the open operation from its journal: reopen unchanged, continue forward or stop in `needs_operator` (`RESUME <op8>`) |
| `sudo pf resume --abandon` | Cancel an operation where it is legal (before data effects: owned candidates dropped, the unchanged deployment reopened, `ABANDON <op8>`; a restore while its target is prepared: `ABANDON RESTORE <project> <op8>`) |
| `sudo pf resume --keep-workspace` | Finish an operation without refreshing `repo/` (`KEEP WORKSPACE <op8>`) |
| `sudo pf status --operation ID` | One operation in detail: effects, evidence, attempts, children |
| `sudo pf resume --operation ID --acknowledge` | Acknowledge runner records of an operation directory without a journal (`ACKNOWLEDGE <op8>`; section 15) |
| `sudo pf cleanup` | Report recorded leftovers of closed operations (read-only) |
| `sudo pf cleanup --apply` | Remove the default leftover set (`CLEANUP <project> <op8>`; section 16) |
| `sudo pf cleanup --apply --recovery-target P \| --checkpoint-history NAME \| --generation WSG` | Also remove one selector-only item (its own phrase) |
| `... deploy\|update\|rollback\|restore-instance --keep-workspace` | Run without the workspace generation switch |
| `sudo pf ps [options] [SERVICE...]` | Read-only Compose container view of the instance (section 14) |
| `sudo pf logs [options] [SERVICE...]` | Bounded, redacted service logs (section 14) |
| `sudo sh ./deploy/synology/install-control.sh init --root <root>` | Initialize a new protected installation root (section 5 (a)) |
| `sudo <root>/bootstrap/pf install status` | List install operations, their phase and next step (read-only) |
| `sudo <root>/bootstrap/pf install register …` | Register an instance with existing directories (`REGISTER <slug>`) |
| `sudo <root>/bootstrap/pf install migrate-legacy …` | Copy legacy v2.5 configuration and register it; v2.5 stays in control (`MIGRATE <slug>`) |
| `sudo <root>/bootstrap/pf install control --source DIR \| --release ID` | Install or select a control release (`INSTALL CONTROL`/`SELECT CONTROL <id>`) |
| `sudo <root>/bootstrap/pf install resume [--operation ID] [--abandon]` | Continue or abandon an open install operation (`RESUME`/`ABANDON <op>`) |
| `sudo <root>/bootstrap/pf config admin --configuration DIR [--project P]` | Create or complete `pf-config.json` before registration (no `--instance`; schema 2 only) |
| `sudo pf --instance <slug> config admin` | Create, migrate (schema 1 → 2) or complete the instance's `pf-config.json` (`[y/N]`) |
| `sudo pf --instance <slug> config app` | Create or complete the instance's `.env` for its profile (`[y/N]`) |

Commands started without a terminal must pass `--instance <slug|uuid>`; until PF-A4.3 policy
grants exist every locked command is refused without a terminal (`terminal-required`,
`policy-grant-required`). Bare `pf permissions` is refused (`permissions-verb-required`, exit 2); its
former repair is `pf permissions apply`.

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
- the Compose `run` one-off labels that `fail_closed` relies on are proven offline only;
- there is no managed route for an application CLI in the backend container.

PF-A2.1 limits:

- offline only: nothing was installed on, or run against, a real NAS, DSM host or Docker daemon;
- the real global launcher `/usr/local/bin/pf` is untested (tests use an isolated launcher path);
- A2-T04 with a real Docker daemon is not run (offline identity evidence only);
- no power-loss or reboot durability claim beyond fsync and rename on the tested filesystem;
- one control release per installation root;
- the launcher and verifier bytes are frozen until PF-A4.3 (`bootstrap-change-unsupported`);
- no legacy Docker resource adoption, v2.5 control retirement or v2.5 state import;
- no configuration creation (PF-A2.2) and no permission change (PF-A2.3);
- old releases and discarded registrations are not cleaned up (PF-A5.1).

PF-A2.2 limits:

- offline only, as for PF-A2.1;
- the backend image's zone data is not checked (owner unassigned); the backend still refuses an unknown zone
  at startup;
- configuration files with ACL entries are refused and need manual edits; the SMB race between the
  wizard's final comparison and its rename cannot be closed by a lock;
- a password that needs URL encoding fails at the backend migration step until the app-lane `env.py` fix is
  deployed;
- a `backup_read_group` change takes effect without revision-bound approval (PF-A2.3).

PF-A2.3 limits (the permission compiler and the filesystem behaviour are tested offline on a real Linux
filesystem as root in a throwaway container; no DSM or SMB claim):

- `effective_access_verified` and `future_file_behavior_verified` are never verified: no SMB account test, DSM
  Shared Folder permission, DSM ACL or share create mask was observed (PF-A5.1);
- ACL-bearing scopes cannot be bulk-applied, no ACL is ever written, and fresh entries that inherit an ACL stop
  protected flows and keep their creation mode in editable scopes;
- the editor freeze does not stop root services or memory-mapped files; a custom Docker data root on the same
  device inside a scope is not detected offline;
- until the first approval `workspace_write_group` still sets the group of files pf creates in the editable
  scopes; workspace script execution "none" is not activatable;
- the control release is checked, never changed, and fixed at No group access;
- ACL-bearing configuration files stay refused by the config wizards (PF-A5.1);
- an invalid permission policy record has a manual recovery route only (section 16).

PF-A3.1 limits (offline and filesystem evidence only; Docker and PostgreSQL are simulated):

- no real Docker, Compose or PostgreSQL behaviour is claimed; the emergency-preservation case A3-T03 is blocked at
  its required real Docker/PostgreSQL level (PF-A3.4);
- *(superseded by PF-A3.3, see its limits below)* the instance purge was gated on a `data_restore_verified` record of
  the purge bundle's own payloads; since PF-A3.3 it is gated on a `functional_recovery_verified` record from an
  isolated topology (fake-daemon evidence only);
- *(superseded by PF-A3.3)* emergency preservation was wired into `rollback` and `backup --emergency` only; since
  PF-A3.3 it also covers `reset-db` and `abort-deploy`; the instance purge stays healthy-gated and `restore-instance`
  has no current data to preserve;
- unsealed staging and superseded deployments are never cleaned (PF-A3.2/PF-A5.1); the workspace replacement stays
  in place (PF-A3.2); archived database image layers are used at restore since PF-A3.3;
- a downgrade to the PF-A2.3 control is unsupported while any schema-1 bundle exists;
- checksums prove integrity, not authorship: bundles have no trust anchor beyond the protected root-owned
  directories.

PF-A3.2 limits (offline, filesystem and installed-CLI evidence with a fake Docker daemon; nothing ran against DSM,
btrfs, SMB, a real Docker daemon or PostgreSQL):

- A3-T04 and A3-T05 are blocked at their real Docker/PostgreSQL level (PF-A3.4); A3-T10's DSM/btrfs/SMB part is
  blocked (PF-A5.1); the installed-CLI restart cases run against a simulated application plane of the fake daemon
  under a test-only release source (see `TEST_REPORT.md`);
- a lost live-migration result with unchanged heads is never retried (`needs_operator`) until a profile declares
  transactional upgrades (PF-A4.1);
- a SIGKILL between a child's start and its `children.json` record leaves only the daemon and database probes for
  that child; the database probe counts every client session, so a transient one refuses until a later `resume`;
- workspace staging durability relies on `os.sync()` before the renames; power loss is not proven (PF-A3.4);
- an ACL-bearing workspace root, a mount-point workspace or an unsafe container ends updates in
  `workspace_sync_pending` until `--keep-workspace` or a fix;
- `restore-instance` has no abandon once its data restore started; `backup --emergency` stays journal-less
  (*superseded by PF-A3.3*: `abort-deploy` after the frontend opened preserves first and is allowed; side-by-side
  restore is a journaled `restore-side-by-side` operation);
- *(superseded by PF-A3.3: `pf cleanup`, section 16)* partial capture folders, `pf_verify_*` leftovers of a failed
  verification, superseded stages, a displaced checkpoint history and retained workspace generations are now removed
  on request; image tags loaded by an abandoned restore are still only reported, never cleaned;
- runner records of operations without a journal refused `pf install` until PF-A3.3, which adds the acknowledgement
  route (section 15); besides `backup --emergency` they include every lifecycle child
  interrupted or timed out before the confirmation (candidate `compose build`, `compose run` contract checks,
  `ensure_local_contract`);
- more than 20000 entries in `operations/` refuse every mutating route until closed operations are archived.

PF-A3.3 limits (offline, filesystem and installed-CLI evidence with a fake Docker daemon; nothing ran against DSM, a
real Docker daemon or PostgreSQL):

- A3-T06, A3-T07, A3-T08, A3-T15, A3-T16 and the Docker-storage part of A3-T14 are blocked at their real Docker/
  PostgreSQL level until PF-A3.4; the filesystem part of A3-T14 uses the real `statvfs`;
- isolation of the `pfverify-*`/`pfrecover-*` topologies is proven from the daemon's reported configuration only
  (internal network, port bindings, restart policy, mounts); real network, port and egress behaviour is unobserved;
- the application invariant oracle is `app.cli reconcile` of the deployed image; an image without it is recorded as
  `unavailable`, and the reconcile output itself is never stored;
- owner decision, applied: the instance purge writes the registry tombstone `state: purged` and releases the project
  claim (OD-A33-08, design-conforming);
- declared deviations from design r3 LIFECYCLE §9, **awaiting owner approval** (each fails closed): a recovery target
  has no listener and is reached only with `docker exec` as root (OD-A33-06); it is recorded by its own
  `restore-side-by-side` operation, not as a registered instance (OD-A33-07); another instance's bundle, including one
  from a lost host, is refused before any confirmation (`restore-target-mismatch`, OD-A33-09). PF-A3.3 acceptance is
  conditional on that approval;
- the dispositions replace three PF-A3.2 limits above: `abort-deploy` after the frontend opened preserves first,
  emergency preservation covers `reset-db` and `abort-deploy` (the instance purge stays healthy-gated because its
  final bundle must be functionally verifiable; `restore-instance` has no current data to preserve, its target is
  empty), and runner records without a journal have the acknowledgement route.

**PF-A1 closure (offline).** With PF-A1.4 every entry route uses the A1 primitives (explicit
instance, one runner, daemon binding, Compose envelope, exact inventory) and no catch-all Compose
route remains; the PF-A1 safety scope is proven offline only. A1-T11…T14 stay blocked on a real
Docker daemon (owners PF-A3.4/PF-A5.1, including the real Compose `run` one-off labels) and A1-T17
on DSM ACL/SMB evidence (owners PF-A2.3/PF-A5.1). No finding is closed overall and nothing here
is production-ready.

Before relying on v2.5 recovery on important data, perform at least one disposable staging
cycle on the actual NAS, interactively. A v2.5 NAS path requires legacy adoption (OD-A21-05) before a
live v2.5 NAS is migrated.

```text
install-control.sh init
→ pf config admin --configuration <config> --project <project>
→ pf install register
→ pf config app
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
