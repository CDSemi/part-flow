# Deployment Admin (`pf`) Guide

> **Language:** English is the source of truth. [Tiếng Việt](./SYNOLOGY_ADMIN.vi.md).

This guide describes the Deployment Admin, the command-line tool `pf` in `deploy/synology/` that installs and manages
PartFlow **staging** instances on a Synology NAS or another Linux host with Docker. It is written for the administrator
of such a staging instance. Production never uses `pf`: production is installed and operated with
[`DEPLOYMENT.md`](../DEPLOYMENT.md), [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) and
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

The messages of `pf` cite this guide by section number (for example `SYNOLOGY_ADMIN §16`), so the section numbers
stay fixed.

## 1. Purpose and status

`pf` keeps two things apart. The **workspace** is the editable PartFlow checkout that trusted users change over Server
Message Block (SMB) file sharing. The **control release** is the code that `sudo pf` runs: it is installed once from a
reviewed tree, owned by root and verified before every run, so the people who can edit the workspace can never change
what runs as root. Every lifecycle step (deploy, update, backup, rollback, database reset, purge and restore) runs from
a written plan, asks for a typed confirmation and records each effect in an operation journal, so an interrupted step
can be continued or cancelled safely with `pf resume`.

Status:

- Development of `pf` stopped on 2026-10-14, when the owner decided to rebuild the deployment kit as a separate
  project. The tool stays in `deploy/synology/` in its last state, and its tests still run.
- `pf` manages staging only. A staging instance never runs on the Docker daemon of the production host: the production
  release script `release.sh` refuses to run while a `partflow-staging` Compose project exists on its daemon
  ([`DEPLOYMENT.md`](../DEPLOYMENT.md) §6).
- `pf` has never run on a real NAS. Its evidence is an offline test suite and one run with a real Docker Engine, Docker
  Compose and PostgreSQL inside an isolated Linux container (§18). Treat the first staging run on a NAS as its first
  real test.

Requirements: a Synology NAS with DiskStation Manager (DSM) or another Linux host, a local rootful Docker daemon with
Docker Compose v2 (the `docker compose` plugin, or a standalone Compose binary registered at installation; Compose v1 is
not supported), Git, and a Python interpreter of version 3.9 or newer. `pf` uses the Python standard library only.

The **installation root** holds the control and the private state of every instance:

```text
<root>/
├── bootstrap/                 launcher pf, verifier pf_bootstrap.py, bootstrap.conf, tools.conf, scheduler wrappers
├── releases/r-<16 hex>/       control releases: immutable, content-addressed, one of them bound
├── install-operations/        the plan and journal of every install operation
├── profiles/, policies/       the PartFlow staging profile and the staging policy
├── registry/, locks/          registered instances, reservations and locks
├── instances/<uuid>/          private state of one instance: record, state, operations, artifacts
└── sources/, staging/, home/  protected Git source store, staging area, private home of child processes
```

`<uuid>` is the universally unique identifier (UUID) of an instance; `pf status` and `pf instances` print it. Each
instance is registered with four folders that the administrator chooses:

| Folder | Content | Access for the chosen group (§2) |
| --- | --- | --- |
| Workspace | The editable PartFlow checkout, usually named `repo/`. | Read and edit |
| Configuration | `pf-config.json` and the application `.env` (§6). | Read and edit |
| Backups | Revision checkpoints (§10). | View and copy |
| Recovery | Purge recovery bundles (§12). | View and copy |

In this guide `pf` stands for the installed launcher run as root: `sudo <root>/bootstrap/pf`, or `sudo pf` when the
installer created the global launcher `/usr/local/bin/pf` for this root (§5). With more than one registered instance,
add `--instance <slug>` before the command; a command started without a terminal must always name its instance.

Every child process that `pf` starts (Git, Docker and Compose, the PostgreSQL client programs inside the `db`
container, host diagnostics) goes through one runner: only the tools registered in `<root>/bootstrap/tools.conf` run,
with a fixed environment, a deadline and bounded output from which the database password is removed. An interruption
(Ctrl-C, a closed Secure Shell (SSH) session, `SIGTERM`) stops the child processes, records what happened and leaves
the operation for `pf resume`.

## 2. Permission model

Each instance follows one permission policy. You choose a group and an access level for each scope, and `pf` computes
the file modes; it never asks for an octal mode.

| Choice | Value in the policy | Members of the group can |
| --- | --- | --- |
| Read and edit | `read_write` | Read, create, edit, rename and delete. |
| View and copy | `read_only` | Read and copy. |
| No group access | `none` | Nothing; only the owner has access. |

| Scope | Folder | What can be chosen | Never changed by a choice |
| --- | --- | --- | --- |
| Workspace | The registered workspace. | Group, any access, inheritance, script execution `Owner only` or `Owner and group`. | Existing editor owners are kept; only files that the deployed source marks executable get an execute bit. |
| Configuration | The registered configuration folder. | Group, any access, inheritance. | Nothing is executable. |
| Control release | `<root>/releases/<id>/` | Fixed: No group access. | Owner root, no group or other write, no special bits; checked, never changed. |
| Backups | The registered backups folder. | Group, View and copy or No group access. | Owner root, no group write. |
| Recovery bundles | The registered recovery folder. | As backups. | As backups. |
| Private state | `<root>/instances/<uuid>/` | Fixed: owner only. | Folders `0700`, files `0600`. |

The resulting modes (the owner always reads and writes, others get nothing, setgid only on folders with inheritance):

| Access | File | Folder | Folder with inheritance |
| --- | --- | --- | --- |
| No group access | `0600` | `0700` | `2700` |
| View and copy | `0640` | `0750` | `2750` |
| Read and edit | `0660` | `0770` | `2770` |

A file that the deployed source marks executable also gets owner execute (`Owner only`) or owner and group execute
(`Owner and group`).

**Approved and derived policy.** A confirmed `pf permissions apply` writes the next *permission policy revision* to
`<root>/instances/<uuid>/permission-policy.json` (root, `0600`); `purge` keeps it. Until the first approval the instance
uses a derived policy: the workspace and the configuration folder get the `workspace_write_group` of `pf-config.json`
with Read and edit and inheritance, and backups and recovery bundles keep the group their folders already have, with
View and copy. The groups in `pf-config.json` are proposals: `backup_read_group` never changes access by itself, and
`workspace_write_group` stops applying once a revision exists. A differing proposal is reported
(`permission-group-proposal`) and takes effect only through `apply`. Members of the backup group can read the database
contents and the credentials kept in recovery bundles.

```sh
pf permissions check [--scope SCOPE]...
pf permissions plan  [--scope SCOPE]... [--details]
pf permissions apply [--scope SCOPE]... [--details]
pf permissions apply --resume | --abandon
```

`SCOPE` is `workspace`, `configuration`, `control`, `backups`, `recovery` or `private_state` (repeatable; all scopes by
default).

- `check` and `plan` only read: they take no lock and write nothing. `check` exits 1 when anything differs
  (`permissions-differ`). `plan` shows, for each scope, the policy, the group members, the counts, whether an editor
  freeze is needed, the blockers and a plan hash; `--details` lists each change.
- `apply` takes the instance lock, asks for the group, the access, the inheritance and, for the workspace, the script
  execution, shows the changes and the plan hash, and asks for `APPLY PERMISSIONS <slug>`. It records its intent,
  applies and verifies every selected scope, and only then writes the new revision. An interrupted apply blocks every
  other change to the instance until `apply --resume` or `apply --abandon` (`permissions-apply-pending`).
- `apply` never follows a link, never crosses a mount, never changes a hard-linked, special, access control list (ACL)
  carrying or `@docker` entry, and never changes the control release. Each of these is a blocker that `plan` reports,
  and `apply` changes nothing while one exists.

**Editor freeze.** A change below the top folder of the workspace or the configuration needs a verified editor freeze.
`apply` first makes the scope folder owner-only, so editors can no longer enter it, then looks in `/proc` for any
process that still uses an entry. A process that does stops the apply (`editor-freeze-refused`, nothing changed); start
`apply` from outside the scope folders, for example after `cd /`. The freeze is unavailable when `/proc` cannot be read
or the scope folder carries an ACL (`editor-freeze-unavailable`). Then either leave the scope out with `--scope`, or
change it by hand: stop SMB editing of the folder, set the modes of the table above as root, and run
`pf permissions check`. The freeze does not stop a root service or a memory-mapped file.

**ACLs and DSM.** `apply` never writes or removes an ACL entry. An entry with an access ACL is a blocker
(`scope-entry-acl`); a folder with only a default ACL is allowed. DSM Shared Folder permissions still apply on top of the
file modes: check them for each SMB account in the DSM Control Panel. A DSM shared folder that uses ACLs therefore
cannot be changed in bulk by `apply`.

**Statuses.** `check` and `apply` print three statuses per scope: `mode_applied`, `effective_access_verified` and
`future_file_behavior_verified`. The last two always read `not verified`, because `pf` computes them from file modes
only; files created over SMB take the client's or the share's mask.

**Trust.** Write access to the workspace and the configuration folder lets a trusted user change the application source
and the runtime settings, including the database password in `.env`. Checkpoints and recovery bundles contain
application data, and recovery bundles also contain `.env`. Grant SMB access only to people who may see and change all
of this.

## 3. Where `.env` lives

The application `.env` lives in the configuration folder, outside the workspace. The application does not depend on
where the file is stored, because `pf` hands Compose every input explicitly:

- every changing command first freezes `.env` into a private snapshot in its operation folder and gives Compose that
  snapshot; an edit of `.env` during the operation is detected;
- `PARTFLOW_REPO_ROOT` names the workspace for the image builds;
- `PARTFLOW_DATABASE_URL` carries the backend database address with percent-encoded credentials;
- `DEPLOY_ADMIN_INSTANCE_ID` carries the instance identity that labels every container, volume, network and image that
  Compose creates; it comes from the protected registration and is never read from `.env`.

Read-only views give Compose a protected empty environment file and the allowed values instead, so Compose never reads
an editable file. Keeping `.env` outside the workspace also keeps it out of Git and lets `pf` replace the workspace
without touching credentials. A raw `docker compose` command run in the workspace finds neither the `.env` nor the
installed Compose file; §14 shows the explicit form.

## 4. Source files and the installed control

The repository holds the sources of the tool: `pf.sh` and `compose.nas.yaml` at the repository root, and in
`deploy/synology/` the installer `install-control.sh`, the controller `pf-admin.py` with its modules `pf_*.py`, the
scheduler wrappers `backup.sh` and `release-check.sh`, the examples `pf-config.example.json` and `nas.env.example`, the
record schemas in `contracts/`, the tests and `TEST_REPORT.md`.

None of these repository files runs during normal administration. `install-control.sh init` (§5) is the trust
boundary: it reads the reviewed files as data, shows what it will install, and installs exactly that as a control
release under `<root>/releases/`. The installed launcher runs the bootstrap verifier before any control code; the
verifier checks the interpreter, the configuration, every parent folder and the whole pinned release tree. The
repository `pf.sh` refuses to operate; use the installed launcher.

An application `update` never changes the control. When a later PartFlow revision changes `pf-admin.py`,
`compose.nas.yaml` or another tool file, review that revision and install it with `pf install control` (§15).

## 5. Installing

Until the global launcher exists, call the installed launcher by its full path, `<root>/bootstrap/pf`. `sudo pf`
reaches the same root only when `init` created `/usr/local/bin/pf`; an existing file at that path, for example the
launcher of an older installation, is never replaced.

### (a) New installation

1. From a reviewed checkout, create the installation root. The path must be absent, or an empty folder on its parent's
   filesystem; a mount point or a DSM shared folder itself is refused (`root-exists`).

   ```sh
   sudo sh ./deploy/synology/install-control.sh init --root <root>
   ```

   Missing values are asked; `--interpreter`, `--tool <id>=<path>`, `--launcher-path <path>` and `--no-launcher`
   override the detected defaults. Confirm with `INSTALL CONTROL <release-id>`. The root is built beside the target and
   published with one rename, so a failed or interrupted `init` publishes nothing: run it again.
2. Create the administration configuration in an existing, root-owned configuration folder:

   ```sh
   sudo <root>/bootstrap/pf config admin --configuration <config> --project <project>
   ```

   It asks for the workspace group and the backup group from the host's existing groups and writes
   `<config>/pf-config.json` after you answer `y` (§6).
3. Register the instance with its four existing folders:

   ```sh
   sudo <root>/bootstrap/pf install register --slug <slug> --project <project> --workspace <checkout> \
     --configuration <config> --backups <backups> --recovery <recovery>
   ```

   The preflight checks the folders (it creates none), the configuration, the groups, overlaps with other instances and
   the Docker daemon, and lists every conflict at once. Confirm with `REGISTER <slug>`.
4. Create `.env` with `pf --instance <slug> config app`, or let the first `pf --instance <slug> deploy` ask the same
   questions (§6, §7).

### (b) Registering a v2.5 home

An installation in the older v2.5 layout (a home folder with `repo/`, `control/`, `config/`, `backups/` and
`recovery/`) can be registered without moving its data:

```sh
sudo <root>/bootstrap/pf install migrate-legacy --legacy-home <home> --workspace <checkout> --slug <slug>
```

The command copies a `.env` or `pf-config.json` that exists only in the checkout into `<home>/config/` byte for byte
(the original stays where v2.5 reads it), refuses when two differing copies exist, and registers the instance after
`MIGRATE <slug>`. It migrates nothing else: no v2.5 state, no Docker resources and no permissions. While the v2.5
`control/` folder exists, v2.5 remains the control of that instance: `pf` refuses every change to it
(`legacy-control-active`) and offers `status`, `doctor`, `ps` and `logs`. `pf` never adopts the Docker resources of a
v2.5 installation.

## 6. Configuration files

### `pf-config.json`

The administration settings of one instance. `pf config admin` creates, migrates or completes the file (§5 before
registration, `pf --instance <slug> config admin` after it). The current form, schema 2, lists every key:

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

| Key | Meaning |
| --- | --- |
| `repository`, `branch` | The GitHub repository and the branch that `--latest` resolves. |
| `project` | The Compose project; it must equal the registered one (`admin-config-mismatch`). |
| `environment` | A label that must equal the instance's approved policy environment (`staging`); editing it never changes the policy. |
| `release_channel` | The default channel, `stable` or `prerelease`, for `--release latest` and `release-check`. |
| `auto_update` | A proposal only: nothing runs unattended (§13). |
| `ci_workflow` | The continuous integration (CI) workflow file whose result `deploy` and `update` check. |
| `health_timeout_seconds` | How long the health checks may wait. |
| `minimum_free_mb` | The free space kept on every device (§10). |
| `backup_read_group`, `workspace_write_group` | Group proposals (§2). |

- A file without `schema_version` is the legacy schema 1: omitted keys take their schema 1 values, and it keeps
  working. Only `pf --instance <slug> config admin` migrates it to schema 2: explicit values are kept and the omitted ones
  are written with their schema 1 values. A value that schema 2 refuses blocks the migration
  (`admin-config-migration-blocked`).
- The wizard asks only for the two groups: both when it creates the file, otherwise only a group that does not exist on
  the host. Answer with a number from the list of the host's groups or with an existing group name; groups are never
  created. Change the other keys by hand. `pf` checks every key before use and refuses a duplicate, unknown, missing or
  mistyped key.
- The file is replaced through a temporary file `.pf-config.json.pf-config-<8 hex>` beside it, keeps the owner, group and
  mode of the file it replaces, and is not written when the file changed after the summary (`config-changed`).

### `.env`

`.env` holds exactly seven keys:

```dotenv
POSTGRES_USER=partflow_staging
POSTGRES_PASSWORD=<generated-secret>
POSTGRES_DB=partflow_staging
SITE_TIMEZONE=America/Los_Angeles
PARTFLOW_BIND_IP=192.168.0.11
PARTFLOW_HTTP_PORT=5173
PARTFLOW_ALLOWED_HOST=localhost
```

`pf --instance <slug> config app` creates or completes it, and the first `deploy` without `.env` runs the same wizard. A
new file starts from the installed `nas.env.example`; in an existing file only the changed lines are rewritten. The
wizard asks only for missing or invalid values: the database user, the database name, the factory timezone, the access
mode (direct access on a local area network (LAN) address, or the DSM Reverse Proxy on `127.0.0.1`), the HTTP port, and
the Reverse Proxy host name.

- **Password.** An existing `POSTGRES_PASSWORD` is never shown, changed or regenerated. A missing one is generated (64
  hexadecimal characters from a secure random source) only while the instance has never been deployed. A kept password
  shorter than 32 characters is reported (`password-weak-for-new-deployment`), and the first `deploy` refuses it.
- **Deployed instances.** Once an instance has been deployed (or migrated from v2.5), the three database values are
  never asked, generated or rewritten; a missing or unusable one is refused (`app-credential-unusable`). Editing them by
  hand does not change the credentials inside the existing database, and no wizard rotates them.
- **Timezone.** A new `SITE_TIMEZONE` must exist in the host's time zone data.
- **Strict format.** One `KEY=VALUE` per line, no spaces around `=`, no `export`, `#` at the start of a comment line; a
  value is unquoted, single-quoted or double-quoted and is never expanded. A value that cannot be passed on literally (a
  single quote, a trailing backslash, a control character) is reported as `migration-issue` and blocks the changing
  commands until you fix the file by hand.
- **ACLs.** A `.env` or `pf-config.json` that carries ACL entries is refused (`config-file-acl`); edit it by hand.
- **URL encoding.** Application revisions older than commit `4313863` of 2026-10-07, such as the release
  `v0.1.0-alpha.1`, cannot run their migrations with a password that needs URL encoding (any character other than
  letters, digits and `-._~`); `deploy` and `update` of such a revision stop at the migration step. Generated passwords
  are not affected.

## 7. First deployment

```sh
pf deploy --latest                       # the tip of the configured branch
pf deploy --commit <full commit hash>
pf deploy --release <tag>                # or: --release latest [--channel prerelease]
pf deploy                                # the current checkout, only when it is proven (§8)
```

`--skip-ci` skips the CI check as an explicit manual exception; it is not evidence that CI passed. `deploy`:

1. refuses when the target already has containers, volumes or networks (`resource-target-not-empty`);
2. creates or reuses `.env` (§6);
3. resolves the source to an exact commit, fetches it into the protected source store and checks its CI result;
4. builds the backend and frontend images before the database exists;
5. checks the free space (§10) and asks for `DEPLOY <first 12 characters of the commit>`;
6. writes its plan and journal, then archives the exact source into a private staging folder;
7. starts a new PostgreSQL database, runs `alembic upgrade head`, starts the backend and checks its health and schema,
   then starts the frontend and checks `/api/health` through it;
8. seals the **deployment record** (the source archive with its manifest, the resolved Compose model and the frozen
   `.env`; root only, kept even by `purge`) and writes the deployment pointer `deployed.json`;
9. switches the workspace to the deployed tree (§8).

If sealing fails after a healthy start, the application keeps running, the command exits 1 with
`deployment-record-incomplete`, and the next `deploy`, `update`, `rollback` or `restore-instance` seals a record. After
smoke testing the user interface and the workflows, take the first checkpoint with `pf backup` (§10).

An interrupted deploy continues with `pf resume`. To give up an incomplete first deployment, run `pf abort-deploy` and
confirm `ABORT DEPLOY <project>`. It is allowed until the deployment pointer is written, removes only the containers,
network and volume that this deployment created, and keeps the images, the workspace and `.env`, so `deploy` can run
again. When the result of the first migration was lost, the deploy stops in `needs_operator` and `abort-deploy` is the
route. If the frontend had already opened, users may have written data, so the abort first stops the application and
preserves the current database as a `before-abort` checkpoint in the backups folder; the database must be reachable
(`preservation-failed` otherwise). An interrupted abort continues after `RESUME ABORT DEPLOY <project>`.

## 8. Workspace and deployed revision

The workspace is a working copy, not the record of what runs: editing a file over SMB does not change the running
application. `pf status` shows both identities:

```text
Deployed source: <commit>
Workspace: provenance git_commit|unknown | manifest commit <commit or none> | differs from deployed: True/False | changes: ...
Deployment: <deployment-id> (git_commit <sha12>|unknown), sealed <time>
```

The deployed source comes from the deployment record and is printed as a commit only when the protected source store
proved it; otherwise `status` prints `unknown provenance (…)`. The `Deployment:` line can also read
`legacy (no deployment record; created before PF-A3.1)`, `not recorded (seal failed in operation <op>; …)` or
`<id> deployment-artifact-mismatch: <file>: <detail>` (a damaged record, kept as evidence, §16).

- `pf` compares the workspace byte for byte with the manifest it recorded when it deployed that tree. It never runs Git
  in the workspace, and it never trusts the workspace's `.git` folder. A workspace without such a manifest, or one that
  differs from it, has `unknown` provenance.
- The comparison ignores untracked items named `.env`, `node_modules`, `.venv`, `__pycache__` or `.pytest_cache` (and
  `.git`). A commit that tracks a path with one of those names is refused.
- `pf deploy` without a selector proves the checkout first: it fetches the commit that the checkout claims into the
  protected store and requires the workspace to match it exactly; otherwise it asks for `--commit`, `--latest` or
  `--release`.
- No lifecycle command changes the permissions of existing workspace files; use `pf permissions` (§2).

**Workspace switch.** After activation, `deploy`, `update`, `rollback` and `restore-instance` do not copy files into the
workspace. They stage the deployed tree in `<workspace parent>/.pf-generations-<instance-uuid>/`, rename the old
workspace to a retained generation `wsg-<time>-<8 hex>` in that folder, and rename the staged tree to the workspace path.
The old tree is kept whole, untracked files and `.git` included, and a file an editor saved during the switch lands
there. Copy such work back as root, for example
`sudo cp -a <workspace parent>/.pf-generations-<uuid>/<generation>/<path> <workspace>/<path>`. `pf status` counts the
retained generations, and `pf cleanup --apply --generation` retires one (§16).

- Between the two renames the workspace path is briefly absent. If the process stops there, only `pf resume` (bind the
  new tree) or `pf resume --keep-workspace` (put the old tree back) can run.
- When the switch is not possible (the workspace is a mount point or a shared-folder root, the generation folder is
  unsafe, the workspace carries an ACL, or the device lacks room), the operation completes the activation, exits 1 with
  `workspace-sync-pending` and stays open in `workspace_sync_pending` with the application running. Fix the cause and
  run `pf resume`, or keep the current workspace with `pf resume --keep-workspace`.
- A manual `pf backup` may run while a switch waits. While that backup is open, `status` shows
  `waits: backup operation <op> is open; run 'pf --instance <slug> resume --operation <op>' (or add --abandon) first,
  then this operation's routes apply`: finish or abandon the backup first.
- `deploy`, `update`, `rollback` and `restore-instance` accept `--keep-workspace` to skip the switch from the start.

## 9. Updating

```sh
pf update --latest
pf update --commit <full commit hash>
pf update --release <tag>                # or: --release latest [--channel prerelease]
pf update --latest --allow-migrations    # needed when the new revision adds migrations
```

`update` resolves an exact commit, checks CI (unless `--skip-ci`), builds candidate images and checks the migration
contract: a changed or deleted existing migration is always refused. It checks that the running images are those of the
current deployment record (`deployment-image-mismatch`) and that there is room, and asks for
`UPDATE <first 12 characters of the commit>`. It then stages the new deployment, stops application writes, captures and
restore-tests a pre-update checkpoint (a differing workspace is saved in it as `workspace.tar.gz`), rehearses new
migrations on a copy of the database, migrates the live database, starts the new images, seals the deployment record
and switches the workspace (§8). No command ever runs `alembic downgrade`.

After an interruption, `pf resume`:

- before any database change, reopens the unchanged deployment and closes the update as `cancelled`; `pf resume
  --abandon` (`ABANDON <op8>`) does the same;
- after a live database change, continues forward;
- when the result of the live migration was lost, reads the live Alembic heads: heads equal to the target count as done,
  and any other heads stop in `needs_operator` (`effect-unknown`), because a partial change cannot be excluded. Recover
  with `pf rollback <before-update checkpoint> --restore-db`, which preserves the current database first.

`<op8>` is the last eight characters of the operation identifier that `status` prints.

## 10. Backups and rollback

`pf backup` captures a revision checkpoint in `<backups>/revisions/<project>/<checkpoint-id>/` and restore-tests its
database dump in a temporary database:

```text
source.tar.gz       the exact deployed source
workspace.tar.gz    only when the workspace differs from the deployed source
database.dump       the active PostgreSQL database
database.list
manifest.json       what was captured, with every payload's size and hash
manifest.sha256
```

`.env` is not part of a checkpoint; purge recovery bundles keep it (§12).

**Classes.** A capture is a *healthy checkpoint* (the live schema matches the deployed images and the exact source is
known), an *emergency preservation* (the actual data, with the mismatch recorded) or *partial* (an emergency capture
where an image or the source could not be identified). Only a healthy checkpoint is a rollback target
(`checkpoint-not-rollback-target`).

**Verification level.** A separate verification record in `<root>/instances/<uuid>/artifacts/verifications/` gives the
level: `captured` (not tested yet), `failed` (the last restore test failed), `data-restore` (every database restored from
the bundle's own dump and checked) or `functional` (restored and checked in an isolated copy of the application, §12).
`pf backup` prints both:

```text
Checkpoint class: healthy_checkpoint
Verification level: data_restore_verified (record <verification-id>)
```

**Strict reading.** Every command that uses a checkpoint or a bundle first checks the manifest hash, the manifest and the
size and hash of every payload; a file that the manifest does not list is refused. Nothing is extracted or confirmed
before that, and archives are extracted without links, special files, absolute paths or `..` paths.

**Refusals before a capture.** `backup`, `update`, `reset-db` and `purge` refuse a healthy checkpoint when the running
images are not those of the deployment record (`deployment-image-mismatch`) or when the deployed source cannot be proven
(`deployment-artifact-mismatch`). They also refuse, in their preflight, a differing workspace that holds a link, a
special file or more than the archive limits (`workspace-unsupported-entry`, `workspace-archive-limit`; §16).

**Emergency preservation.** When a healthy checkpoint is refused but the data must be kept, run interactively:

```sh
pf --instance <slug> backup --emergency
```

It shows the observed mismatch, asks for `EMERGENCY BACKUP <project>`, captures and restore-tests the current database,
and is never a rollback target.

**Free space.** Before its confirmation every lifecycle command measures the space it needs on each device (backups,
recovery, workspace, artifacts, private state and the Docker data root), adds the `minimum_free_mb` floor once per
device, and refuses a shortfall with `capacity-insufficient`. `capacity-unmeasurable` means the Docker data root could
not be measured; `backup --emergency` still works then. `pf` never deletes anything to make room.

**Listing.** `pf backups --page N` lists the checkpoints, newest first, ten per page:

```text
<n>. <bundle-id>  [<healthy|emergency|partial>|<captured|failed|data-restore|functional>]  <reason>  DB=<heads>  source=<git_commit <sha12>|unknown|claimed <sha12>>  [legacy-format-N]
<n>. <folder>  [invalid: <code>]
```

Checkpoints written before the deployment records existed (formats 1 and 2) are read through a fixed in-memory
conversion and never rewritten; format 1 reads as partial, and the commit that a format 2 checkpoint claims is proven
again before use.

**Rollback.**

```sh
pf rollback                               # choose a checkpoint interactively
pf rollback <checkpoint-id>               # code only, keeps the current data (ROLLBACK <checkpoint-id>)
pf rollback <checkpoint-id> --restore-db  # code and database (RESTORE <database> <checkpoint-id>)
```

- Every rollback first stops application writes and preserves the current database: a healthy checkpoint, or an
  emergency preservation when the schema does not match the images. If the database cannot be captured and
  restore-tested, the rollback stops with `preservation-failed`: nothing was restored, the services stay stopped, and
  `pf resume` reopens the unchanged deployment.
- The checkpoint must be healthy, its backend and frontend images must still exist, and its source must match its
  manifest (`source-manifest-mismatch`). A code-only rollback needs a compatible schema.
- `--restore-db` restores into a new candidate database, checks the heads, locale, extensions, owner and row counts, and
  only then switches; a difference drops the candidate (`checkpoint-incompatible`) and leaves the current database
  unchanged. The previous active database is retained as `pf_keep_*`, so writes made after the checkpoint stay there and
  in the preservation; nothing is merged. The confirmed summary is kept as `operations/<op>/confirmation-summary.txt`.
- `rollback --restore-db` may supersede an open `update`, `rollback` or `reset-db` that stopped the application or needs
  an operator; a code-only rollback may supersede only an update that changed no database.

A new checkpoint gets the backups permissions of the policy in force (§2); an entry that inherits an ACL from its folder
stops the backup (`fresh-entry-acl`). An interrupted backup leaves its operation open; `pf backup` or `pf resume` then
makes a new attempt with a new checkpoint identifier.

## 11. Reset staging data

```sh
pf reset-db
```

`reset-db` keeps the deployed version and activates a clean database migrated to the current Alembic heads. It asks for
`RESET <database>` (for example `RESET partflow_staging`), captures the current database first, creates and migrates a
clean candidate database, switches the database names, and retains the previous active database as `pf_keep_*`, which
is never dropped. It does not delete the PostgreSQL volume. Use `reset-db` to clear test data; use `purge` (§12) to
return to a brand-new instance.

Before the confirmation it decides from what it observes:

- the live heads equal those of the running backend image: the capture is a healthy `before-reset` checkpoint;
- the live heads differ, but the running image is the one that the deployment record names (a schema and image
  mismatch): the capture is an **emergency preservation**, and the clean database is migrated to the running image's
  heads. That preservation is data and evidence, never a rollback target. To bring the old data back, restore the
  retained `pf_keep_*` database manually: `pf` has no command that switches back to it;
- the running image is not the recorded one: refused with `reset-deployment-image-mismatch` (bring the deployment back in
  line with `update` or `rollback` first);
- the image contract cannot be read: refused with `reset-contract-unknown`.

A capture failure stops with `preservation-failed` before anything changes. After an interruption `pf resume` redoes the
candidate, and `pf resume --abandon` before the switch drops it and reopens the unchanged deployment. A switch whose
names prove that it never started is redone; one that left the names half renamed stops in `needs_operator`
(`database-switch-unknown`); recover with `pf rollback <checkpoint> --restore-db` to a healthy checkpoint. A reset
started on a schema and image mismatch cannot reopen the old deployment: `pf resume` completes it, and
`pf resume --abandon` drops the candidate and leaves the services stopped; then run `pf reset-db` again, or
`pf rollback <checkpoint> --restore-db` to a healthy checkpoint.

## 12. Purge, recovery and clean redeploy

`pf purge` saves everything needed to rebuild the instance into a verified recovery bundle, then removes the instance's
Docker resources:

```sh
pf instances                       # list the registered instances
pf purge                           # asks whether to delete the checkpoint tree too
pf purge --keep-backups            # keep the revision checkpoints
pf purge --delete-backups          # delete them; they are archived in the bundle
pf purge --reset-admin-config      # also delete pf-config.json
```

The sequence:

1. **Plan.** `pf` lists the instance's containers, volume, network and image tags exactly, by their instance label. A
   resource it cannot claim (unlabeled, foreign, shared or conflicting) refuses the purge with `resource-blocked` before
   anything happens.
2. **Summary and `PURGE <project>`.** Application writes stop.
3. **Bundle.** The recovery bundle is captured and every database is restore-tested. The source database is checked
   with the application's own invariant command (`app.cli reconcile`); an error stops the purge (`app-check-failed`).
4. **Isolated verification.** The exact bundle is restored into a throwaway Compose project `pfverify-<12 hex>` with its
   own volume, an internal network, no published port and a generated database password, and checked: databases,
   health, images, source, configuration and the same reconcile result as the source. A failed check cancels the purge
   (`functional-verification-failed`), reopens the application and keeps the stopped copy for inspection.
5. **Final plan and confirmations.** The binding deletion plan is printed and frozen. Confirm `DELETE <database>`, then,
   when chosen, `DELETE BACKUPS <project>` and `RESET ADMIN CONFIG <project>`, and finally
   `ERASE <project> <random challenge>`. There is no bypass.
6. **Deletion.** The containers, the network, the volume and the image tags that the bundle covers are removed one by
   one, each inspected again right before its removal; a change stops with `plan-drift`. Nothing is pruned,
   `compose down -v` is never used, image removal is never forced and bind-mounted paths are never deleted. The
   guarantee assumes that no other Docker administrator acts at the same time.
7. **Registry.** The instance record is marked `state: purged` and releases its Compose project, so another instance may
   register it. The record, its identity and its permission policy remain.

The bundle is stored in `<recovery>/<project>/purge-<time>-<sha12>-<suffix>/`:

```text
source.tar.gz, workspace.tar.gz          deployed source; the workspace when it differs
configuration/.env                       the runtime environment (secrets)
configuration/pf-config.json             the administration settings
images.tar                               application and database images, by image ID
postgres-globals.sql                     evidence only, never executed
databases/active.dump, databases/<retained>.dump
revision-checkpoints.tar.gz              the checkpoint history
deployment/deployment-record.json, deployment/compose-resolved.json
state/*.json, manifest.json, manifest.sha256
```

**Interrupted purge.** Before `ERASE`, `pf resume` and `pf resume --abandon` both reopen the unchanged deployment. After
`ERASE`, `pf purge` or `pf resume` continues after `RESUME PURGE <project> <bundle-id>` with the remaining planned items
only; nothing new is ever added. When the recovery bundle no longer reads or verifies, deletion stays blocked (§16).

**Brand-new deployment after a purge.** Run `pf deploy --latest`. The purge removed `.env`, so the wizard creates a new
one with a new database password. After `--reset-admin-config`, run `pf --instance <slug> config admin` first.

**Exact restore of a purged instance.**

```sh
pf recoveries [--page N]
pf restore-instance <bundle-id>
```

The target must be empty (`resource-target-not-empty`). Confirm `RESTORE INSTANCE <project>`, then
`RESTORE <database> <bundle-id>`. The restore recreates the workspace, `.env`, the images, every database and the
checkpoint history, checks health, compares the application invariants with the bundle (`Application invariants: …`,
evidence only) and seals a new deployment record that names the bundle. The current control release and the current
`pf-config.json` stay in force; the bundle's copy of `pf-config.json` is kept for comparison. An existing `.env` with other
content is kept as `.env.proposal-<op8>`, and a differing checkpoint history is moved aside to
`<backups>/revisions/<project>.pre-restore-<op8>`. `pf resume` continues an interrupted restore forward;
`pf resume --abandon` (`ABANDON RESTORE <project> <op8>`) is possible only while the target is being prepared.

- A bundle restores only into the instance whose identity it records. A bundle of another instance, including one from a
  lost host, is refused with `restore-target-mismatch`: it would need an import route with policy approval, which this
  control does not provide. A legacy bundle without a recorded identity is accepted only for the same Compose project.
- The paths recorded in a bundle are provenance only; the restore always writes the selected instance's folders.
- When the daemon has no `postgres:16` image, the bundle's database image is loaded from `images.tar` and tagged
  `postgres:16`; that tag is shared by every instance on the daemon.
- A purged record is registered again, unless another instance holds the project (`instance-claim-taken`).

**Side-by-side recovery.** To inspect or export the data of one of the instance's own bundles while the instance keeps
running:

```sh
pf restore-instance <bundle-id> --side-by-side
```

Confirm `RESTORE COPY <bundle-id>`. The bundle is restored into a kept recovery target, a separate Compose project
`pfrecover-<12 hex>` with its own volume, an internal network, no listener and a generated database password, and it is
checked as in the purge. The running instance is not changed. `pf status` lists the target; inspect it as root, for
example:

```sh
sudo docker ps --filter label=com.docker.compose.project=pfrecover-0123456789ab
sudo docker exec -it <db container> psql -U <user> -d <database>
```

Remove it with `pf cleanup --apply --recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`); while it exists the
instance cannot be purged (`isolated-topology-present`). `pf resume --abandon` of an interrupted side-by-side restore
removes the target (`ABANDON RECOVERY TARGET <project>`).

PartFlow data is never merged automatically between databases: Part Movements, quantity lineage, allocations and
reversals follow domain rules that generic SQL inserts would break. Recover old data side by side, and carry anything
forward with an explicit, domain-aware procedure.

## 13. Release checks and scheduled tasks

`pf --instance <slug> release-check [--channel stable|prerelease]` shows, interactively, whether an eligible newer
release exists; apply one with `pf --instance <slug> update --release <tag>`.

Nothing runs unattended on a `pf` instance. Started without a terminal, `backup` and `release-check` are refused with
`policy-grant-required` (exit 20), and `release-check --apply` is always refused with `auto-apply-not-permitted` (exit
20): unattended operation needs a protected policy grant, and no installed policy grants one. `"auto_update": true` in
`pf-config.json` changes nothing. The scheduler wrappers in `<root>/bootstrap/` (`backup.sh --instance <slug>` and
`release-check.sh --instance <slug>`) are refused the same way. Commands that ask for a typed confirmation are refused
without a terminal (`terminal-required`). Take checkpoints by hand with `pf --instance <slug> backup` and copy them off
the NAS.

## 14. Compose views and raw Compose access

`pf` offers two read-only Compose views and forwards no other Compose command:

```sh
pf --instance <slug> ps [-a] [-q] [--services] [--status STATUS] [--format table|json] [SERVICE...]
pf --instance <slug> logs [--tail N] [-f] [-t] [--no-color] [--no-log-prefix] [--since V] [--until V] [SERVICE...]
```

`SERVICE` is `db`, `backend` or `frontend`; `V` is a duration such as `30m` or an RFC 3339 date and time. `--tail`
defaults to 200 lines (1 to 10000), and `logs -f` stops after one hour or 64 MiB of output with `logs-bound-reached`.
Both views take no lock and hide the database password; `pf` without a command is `pf ps`. Any other Compose word is
refused with `compose-route-removed`, which names the managed command to use instead. There is no managed route for an
application command inside the backend container.

When a raw Compose command is unavoidable, for example to run the reconciliation check of
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §7 on a staging instance, give it every input explicitly:

```sh
sudo env PARTFLOW_REPO_ROOT=<workspace> \
  PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<database>' \
  DEPLOY_ADMIN_INSTANCE_ID=<instance UUID> \
  docker compose \
  --project-directory <workspace> \
  --env-file <configuration>/.env \
  -p <project> \
  -f <root>/releases/<release-id>/compose.nas.yaml \
  -f <root>/instances/<instance UUID>/state/active-images.yaml \
  ps
```

`pf status` prints the instance UUID, the installation root and the control release identifier. Replace `ps` with the
command you need, for example
`run --rm --no-deps -T backend python -m app.cli reconcile` for the reconciliation check.

- A raw command bypasses the locks and guards of `pf`. Never run it while `pf status` shows an open operation, never at
  the same time as a `pf` command, and never with a `.env` edited after an open operation froze it.
- A wrong `DEPLOY_ADMIN_INSTANCE_ID` labels new resources with the wrong identity; `pf` then classifies them as foreign
  or conflicting and refuses `purge`, `abort-deploy` and the other guarded commands until they are reviewed.
- Never use `docker system prune --volumes` or `docker volume prune` on a host that may run other workloads.

## 15. Updating the control plane

An application `update` never updates the control. Install a reviewed control release explicitly:

```sh
sudo <root>/bootstrap/pf install control --source <reviewed repository tree>
sudo <root>/bootstrap/pf install control --release r-<16 hex>   # select a retained release again
```

- The summary shows the bound release and the new one, every instance that will be bound to it (one control release
  serves the whole root) and whether the application needs its own operation. Confirm `INSTALL CONTROL <release-id>` (or
  `SELECT CONTROL <release-id>`).
- The release is staged privately and smoke-checked with the registered interpreter: it must import, load the registry
  and every record, and accept the live `pf-config.json` and `.env` of every instance. It is then bound and verified end
  to end through the real bootstrap. If that fails, the previous binding is restored automatically
  (`install-verify-failed`, operation `rolled_back`).
- `app_operation_required: update` means the new release changes `compose.nas.yaml`; the next `pf update` of each
  instance applies the new topology.
- The launcher and the verifier are frozen: a candidate that changes `pf.sh` or `pf_bootstrap.py` is refused
  (`bootstrap-change-unsupported`). A candidate that changes only a scheduler wrapper is `control-unchanged`.
- Installing a release never migrates `pf-config.json`; an older release may refuse a schema 2 file in its smoke check
  (`install-smoke-failed`, nothing bound).
- An interrupted install operation continues with `pf install resume` or is undone with `pf install resume --abandon`.
- `pf install control` is refused while any instance operation is open (`instance-operation-pending`), including an
  invalid journal or an overflowing operation index. Going back to an older release is possible only without open
  operations; an older release does not know the operation kinds and records of newer ones.

This explicit step is the security boundary that lets the workspace stay writable by users: never install an unreviewed
or unknown tree.

**Runner records without a journal.** The runner records every changing child process that timed out or was interrupted
(`unresolved-effects.json` in its operation folder). A closed journal settles the records of its own operation, but
records in an operation folder without a journal keep refusing `pf install` with `instance-effects-unresolved`. Such
folders come from operations of controls older than the operation journal, and from any lifecycle command whose child
process was interrupted or timed out before the confirmation, while no plan existed yet (for example Ctrl-C during the
candidate `compose build`, the `compose run` contract checks of `deploy`, `update` and `rollback`, or
`ensure_local_contract`). Inspect them with `pf status`, make sure that nothing of them still runs, keep the folder, and
acknowledge the records:

```sh
pf resume --operation <op> --acknowledge
```

Confirm `ACKNOWLEDGE <op8>`. `pf` first refuses while an owned one-off container still runs or, when a record names a
database effect, while a client session is open on a database that the records name (`effect-still-running`). It then
writes `operations/<op>/acknowledgement-<hash12>.json`, bound to the current records; a record added later blocks again.
A journaled operation, a folder without records or one already acknowledged is refused with `acknowledge-not-legal`.

## 16. Troubleshooting

Start with:

```sh
pf --instance <slug> status
pf --instance <slug> status --operation <operation-id>
```

`status` prints every open operation before anything else:

```text
Operations: <op> <kind> phase <phase> sequence <n> updated <time> [blocking|needs operator]
  last error: <code>: <message>
  unresolved effect: <eid> <type> <target> (<state>)
  next (recorded at sequence <n>): <command>: <description>; ...
```

It then lists the recent operations, the workspace generations and the runner records; `status --operation` shows one
operation's effects with their state and evidence. Follow only the printed next steps. **Never delete or edit a
journal:** an operation folder is evidence, and its plan is the approval. A refusal that happened before any change ends
with `Nothing was changed.`; a later one names the state it left. After a failed operation `pf` stops only this
instance's own one-off jobs and then its application services, and its message names `pf --instance <slug> status`.

### Operations, journals and resume

| What you see | What to do |
| --- | --- |
| Another operation is open (`operation-open`). | Run one of the legal next steps that the message lists. |
| No automatic continuation is safe (`operation-needs-operator`, `effect-unknown`, `database-switch-unknown`). | Use the printed recovery route: `rollback <checkpoint> --restore-db` after an update, rollback or reset; `abort-deploy` for a first deployment; `backup --emergency` to preserve the data first. |
| A recorded process, an owned one-off container or a database session still runs (`effect-still-running`). | Wait, then run `resume` again; `resume` never stops it. With `effect-probe-unavailable`, `/proc` or the boot identifier cannot be read, so nothing is decided. |
| `resume` could not start the database to observe an effect (`database-unavailable`). | Start Docker, then retry; the operation is unchanged. |
| The note `db-started`. | The `db` container had been stopped from outside; `pf` started that existing container (never a new one) and continued. |
| The instance record, policy, control release, profile or daemon changed after the approval (`plan-authority-changed`). | Restore the approved state. |
| A frozen input changed: a bundle, a checkpoint, the frozen `.env` or settings, or the staged deployment (`plan-input-changed`). | Restore it byte for byte, then `resume`. When the message says that a recovery bundle no longer reads or verifies, deletion stays blocked and the operation stays open: restore the bundle folder byte-identically from an off-NAS copy, then run `resume`. |
| A command asked for other choices than the open operation approved (`plan-inputs-conflict`). | Run it again without those choices, or use `resume`. |
| `abandon-not-legal`, `abandon-in-progress`, `keep-workspace-not-legal`. | A live effect already started, so only `resume` continues; only `resume --abandon` continues an accepted abandon; the new workspace is already bound. |
| The workspace, the retained generation or the stage is not what the journal recorded (`workspace-generation-mismatch`). | Restore the recorded state (for example remove a folder created at the workspace path), then `resume` or `resume --keep-workspace`. With `workspace-validation-failed`, fix the finding, then `resume`. |
| The checkpoint history folder is neither the one from before the restore nor the restored one (`checkpoint-history-unknown`). | Move the foreign tree aside as root, then run `resume --operation <op>`. |
| Another writer changed the journal (`journal-changed`). | Run `status`. |
| More than one operation is open (`operation-conflict`). | Review them with `status --operation`. A backup next to an update that waits in `workspace_sync_pending` is the allowed pair: finish or abandon the backup first (§8). |
| A journal or plan does not validate, or a `state/pending.json` written by an older control exists (`operation-journal-invalid`, `journal-format-unsupported`). | Every changing command is refused; `status`, `doctor`, `backups`, `recoveries`, `ps` and `logs` work. Keep the folder unchanged and review it with `status --operation`. |
| More than 20000 entries in `operations/` (`operation-index-overflow`). | Archive **closed** operations (`completed`, `cancelled` or `failed_preserved`, as `status --operation` shows) as root to a folder outside `operations/`; never move an open or superseded one. Until then every changing command is refused and cleanup candidates are not listed. |
| Runner records block `pf install` (`instance-effects-unresolved`, `acknowledge-not-legal`). | §15. |

### Checkpoints, bundles and deployment records

| What you see | What to do |
| --- | --- |
| The folder was changed or damaged (`manifest-checksum-mismatch`, `manifest-invalid`, `manifest-schema-unsupported`, `bundle-payload-mismatch`, `bundle-unlisted-file`). | Do not edit or repair it; copy it off the NAS as evidence and use another checkpoint or bundle. A folder copied back must be byte-identical (no added `.DS_Store` or `Thumbs.db`). Listings show it as `[invalid: <code>]`. |
| An archive holds a link, a special file or an unsafe path, changed while being read, or does not fit (`archive-member-refused`, `archive-unreadable`, `archive-changed`, `archive-capacity`). | Nothing was extracted. For `archive-capacity`, free space and retry. |
| The differing workspace holds a file over 128 MiB, more than 512 MiB or 200000 files, or a link or special file (`workspace-archive-limit`, `workspace-unsupported-entry`). | Move those files out of the workspace and retry; `backup --emergency` preserves the database meanwhile. |
| The extracted source differs from its manifest (`source-manifest-mismatch`). | Use another checkpoint. |
| The checkpoint is an emergency or partial capture (`checkpoint-not-rollback-target`). | Export its data by hand, and roll back to a healthy checkpoint. |
| The `--restore-db` candidate failed a check (`checkpoint-incompatible`). | Nothing changed: the candidate was dropped and the current database is unchanged. |
| The current database could not be captured and restore-tested (`preservation-failed`). | Preserve it by hand (a `pg_dump --format=custom` of the database from the `db` service to a root-only location) or fix the cause and run `backup --emergency`; then retry, or run `resume` to reopen the unchanged deployment. |
| A file of the deployment record differs from the record (`deployment-artifact-mismatch`). | Keep the folder as evidence; never delete it. As a note, the capture continued with the source proven from the protected store or the workspace. As a refusal, restore the protected source store or roll back to a healthy checkpoint. |
| The running images are not the recorded ones (`deployment-image-mismatch`). | Find out who changed the containers, preserve the data with `backup --emergency` if needed, then redeploy or roll back. |
| The deployment could not be staged, or its store lacks room (`deployment-stage-failed`, `artifact-capacity`). | Fix the detail (often the space or the permissions of `artifacts/deployments`, which needs the source size plus 64 MiB) and retry; nothing changed. |
| The application runs but its record was not sealed (`deployment-record-incomplete`). | The next `deploy`, `update`, `rollback` or `restore-instance` seals one. |
| The purge deletion gate refused after `ERASE` (`purge-bundle-unverified`, `purge-writers-running`, `purge-source-changed`). | The application was reopened; fix the cause shown and run `purge` again. |
| A check of the isolated verification, or the reconcile command on the source database, failed (`functional-verification-failed`, `app-check-failed`). | Nothing was deleted. Inspect the kept copy, then remove it with `cleanup --apply`. |
| A kept `pfverify-*` or `pfrecover-*` project of this instance exists (`isolated-topology-present`). | Run `cleanup --apply`, adding `--recovery-target <project>` for a recovery target. |
| The isolated copy cannot be built on this host, or its random name was taken (`verification-isolation-unsupported`, `topology-name-collision`). | Nothing was deleted; for a name collision, run the command again. |
| `restore-target-mismatch`, `instance-claim-taken`, `registry-busy`. | The bundle belongs to another instance (§12); another instance holds the project; an installation holds the registry lock, so run `resume` after it finishes. |
| Loading `images.tar` would move a tag that names another image (`image-load-would-retag`). | Remove or rename that tag first. |
| A side-by-side target changed while its operation was interrupted and was removed (`recovery-target-lost`). | Run the side-by-side restore again. |
| The notes `verification-record-invalid`, `legacy-manifest-migrated`, `db-image-changed`, `verification-failed`. | Information only. |

### Permissions

| What you see | What to do |
| --- | --- |
| `permissions-verb-required` (exit 2). | Use `check`, `plan` or `apply`. |
| The policy breaks a rule, or asks for workspace script execution "none" (`permission-policy-invalid`, `permission-policy-unsupported`). | Nothing changed; choose again in `apply`. |
| A group of the policy, or the group of the backups or recovery folder, does not exist on this host (`permission-group-missing`). | Choose an existing group in `apply`; groups are never created. |
| `permission-policy.json` is not a root-owned `0600` single file or fails its checks (`permission-approval-invalid`). | It is never replaced automatically and no derived policy is used, so backups and the permission commands stop. As root, move it aside in the same folder, for example as `permission-policy.invalid-<UTC time>.json`; the instance then uses the derived policy, and `apply` writes a new revision 1. |
| A protected-folder finding outside the scopes (`permissions-context-refused`). | Fix what `pf doctor` shows. |
| A blocker in a scope: `scope-path-unsafe`, `scope-entry-link`, `scope-entry-special`, `scope-entry-hardlinked`, `scope-mount-boundary`, `scope-contains-app-storage`, `scope-untrusted-owner`, `scope-entry-acl`, `scope-too-large`. | Remove the link, special file, hard link or ACL, move the mount or the application storage out, or leave the scope out with `--scope`. |
| The control release breaks its limits (`control-ceiling`). | Only `pf install control` changes it. |
| `editor-freeze-unavailable`, `editor-freeze-refused`. | §2, Editor freeze. |
| An apply is not complete (`permissions-apply-pending`, `permissions-entry-changed`, `permissions-verify-failed`, `permissions-interrupted`). | `apply --resume` plans the rest again with the frozen policy; `apply --abandon` undoes the recorded changes in reverse order. With `permissions-already-approved`, only `--resume` can finish it. |
| A line of the permission effect journal cannot be read (`permissions-journal-invalid`). | Nothing changed and the apply stays open. Review `<root>/instances/<uuid>/operations/<op>/permission-effects.jsonl` by hand. |
| A new backup or recovery entry inherited an ACL from its folder (`fresh-entry-acl`). | The operation stopped before publishing it. Remove the default ACL from that folder. |
| `permissions-abandon-conflicts`, `permissions-option-invalid`, `permissions-nothing-pending`. | Objects changed after the interruption are listed and left as they are; `--scope` cannot be combined with `--resume` or `--abandon`; nothing was pending. |
| Results and notes such as `permissions-current`, `permissions-applied`, `permission-policy-unapproved`, `workspace-concurrent-entry`. | Information; run `check` to see the state. |
| SMB users can see the workspace but cannot edit it. | Run `pf permissions check --scope workspace`, then `pf permissions apply --scope workspace` from `cd /`, and check the DSM Shared Folder permissions. |
| SMB users cannot change checkpoints or recovery bundles. | Intended: they stay read-only for the group. Copy them elsewhere for an editable copy. |

### Installation and control

| What you see | What to do |
| --- | --- |
| `sudo sh ./pf.sh ...` refuses to run. | Expected: run `sudo <root>/bootstrap/pf ...`, or create a root first (§5). |
| The preflight lists conflicts as `code: subject: detail` (`install-preflight-refused`). | Resolve every item and run the same command again. With `legacy-env-conflict` or `legacy-admin-config-conflict`, keep the right copy in `config/` and move the other one out. |
| An install operation is open (`install-operation-pending`). | Follow the next step that `pf install status` shows. |
| The instance was migrated from v2.5 and its v2.5 `control/` still exists (`legacy-control-active`). | Make changes with v2.5. |
| `control-binding-changed`, `install-busy`. | A control installation completed while the command started, or another operation holds a lock; run the command again. |
| `resume` found a target that the installer did not write (`install-needs-operator`). | Restore it as the message describes, then run `pf install resume` (or `--abandon` where offered). |
| `install-smoke-failed`, `install-verify-failed`. | The candidate was not activated; for `control` the previous binding was restored. |
| A published root or a used registration cannot be abandoned (`abandon-not-possible`). | Finish it with `pf install resume`. |
| An entry of `<root>/install-operations/` is not a readable operation (`install-operation-unreadable`). | Inspect it as root and move it out of that folder (keep a copy), then retry. |
| `install-interrupted`. | Run `pf install resume`. |

### Configuration wizards

Every refusal below changed nothing.

| What you see | What to do |
| --- | --- |
| `config-option-invalid` (exit 2). | `--configuration` and `--project` belong to `config admin` before registration only. |
| `config-cancelled`, `config-current`. | You declined, or nothing needed to change. |
| `admin-config-required`, `admin-config-invalid`, `admin-config-migration-blocked`. | Create the file with `config admin`, or fix the problem shown by hand; the wizard never repairs a file. |
| The file declares a newer schema (`admin-config-version-unsupported`). | Select the newer control again (`pf install control --release <id>`) or restore the previous file. |
| `admin-config-mismatch`. | Restore the registered `project` or the policy's `environment`. |
| A schema 1 file in an unregistered folder was left as it is (`admin-config-legacy-unregistered`, exit 0). | Register first, then run `config admin` with `--instance`. |
| `.env` does not parse (`app-config-invalid`). | Fix the line that the message names; it never shows the value. |
| The instance was deployed and a database value is missing or invalid (`app-credential-unusable`). | Restore it from the recovery bundle or your records. |
| `migration-issue`, `config-file-acl`, `config-file-unsafe`. | Fix the file by hand (§6). |
| The host has no time zone data to check a new timezone (`zone-data-unavailable`). | Install it, or write the value by hand. |
| `config-changed`, `config-busy`. | The file changed during the wizard, or a registration holds the lock; run the command again. |
| `config-interrupted`, `config-audit-not-recorded` (exit 1). | The file already holds the new content; run the command again to review it. |
| `config-dir-invalid`, `config-path-conflict`, `registry-record-invalid`, `registry-invalid`. | The folder is missing, unsafe or used by a registered instance, or a record cannot be loaded; fix that first. |
| `admin-example-invalid`, `app-example-invalid`, `app-profile-undeclared`, `config-audit-invalid`. | The installed control cannot run the wizard; run `doctor` and reinstall the control release. |

### Command line, terminal and policy

| What you see | What to do |
| --- | --- |
| `compose-route-removed`, `compose-override-refused`, `unknown-option`, `unknown-command`. | `pf` forwards no Compose command and accepts no Compose or Docker option such as `-f`, `-p` or `--env-file`, and abbreviated options are refused. Use the managed command that the message names, or `pf ps` and `pf logs` (§14). |
| `terminal-required`. | Run the command interactively, for example over `ssh -t`. |
| `instance-required-unattended`. | A command without a terminal must name its instance: `pf --instance <slug> <command>`. |
| `policy-grant-required`, `auto-apply-not-permitted` (exit 20). | Unattended operation is not available (§13); run the command interactively. |
| `selection-conflict`. | `--project` names another project than `--instance`; use `--instance` alone. |
| `recovery-outside-instance`, `recovery-state-file-refused`. | Only bundles in the instance's own `<recovery>/<project>/` folder are listed or restored, and a bundle may restore only the state files `deployed.json`, `last-reset.json` and `observed-tags.json`. |
| `logs-bound-reached`. | The output up to the bound is complete; narrow it with `--since`, `--tail` or a service name. |

### Docker daemon, Compose and resources

| What you see | What to do |
| --- | --- |
| Docker is stopped or does not answer (`daemon-unreachable`). | Start Container Manager (or Docker) and retry; `status` still prints every section that needs no Docker. |
| The registered endpoint answers as another engine than the instance is bound to (`daemon-drift`). | Every Docker step is refused. Re-binding is an installation task; never edit the record. |
| `daemon-rootless`, `daemon-info-invalid`, `daemon-endpoint-*`. | Only a local rootful daemon on a protected socket is supported. |
| The resolved Compose model has an unexpected option or a changed image override, or could not be rendered (`envelope-*`). | Nothing was built or started; the message lists each finding with its location (never a value). |
| `resource-blocked`, `resource-not-owned`, `resource-target-not-empty`. | Review the listed resources with `pf status`; unlabeled, colliding or foreign resources are never adopted or deleted automatically. |
| A planned resource changed after the plan was frozen, or containers kept disappearing while being listed (`plan-drift`, `inventory-unstable`). | Retry when the host is quiet; an interrupted purge or abort resumes its frozen plan. |
| `.env` exists, but a raw Compose command reports missing variables. | A raw command reads neither the configuration `.env` nor the generated values; use `pf doctor`, or the full form of §14. |

### Space and leftovers

`capacity-insufficient: <phase> needs <n> MiB on device <d> (<roles>: <paths>), <m> MiB free including the <f> MiB safety
floor` names every role that shares the short device. Free space there (an old `.pre-restore-*` history, exported
files, or the leftovers below); never delete the last healthy checkpoint or a purge bundle to make room. The Docker data
root is shared by every instance on the daemon.

`pf cleanup` lists, without changing anything, the leftovers that closed operations recorded: candidate databases
(`pf_verify_*`, `pf_migrate_*`, `pf_restore_*`, `pf_clean_*`), kept `pfverify-*` projects, failed bundle attempts and
their image tags, superseded stages, and the items that need a selector. A name that only matches a pattern is never
touched. `pf cleanup --apply` removes the default set after `CLEANUP <project> <op8>`; an item that changed or is busy
is kept (`cleanup-items-kept`). Items that need a selector:

- `--recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`);
- `--checkpoint-history <project>.pre-restore-<8 hex>` (`DELETE CHECKPOINT HISTORY <name>`), only when every checkpoint
  in it also exists, unchanged and readable, in the active history;
- `--generation wsg-<…>` retires a workspace generation. First close every editor or SMB session that has files open in
  it (`generation-in-use`). The tree is archived with a seal to `<backups>/generations/<project>/<generation>/workspace.tar.gz`
  and then removed; a link or special file inside refuses (`generation-unsupported-entry`). To get a file back, extract
  it as root outside the workspace, for example `sudo tar -xzf <archive> -C /tmp/restore-<generation> <path>`.

Never cleaned: the `pf_keep_*` databases, the live database, sealed checkpoints, purge bundles, sealed deployments and
the names of open operations.

## 17. Command reference

Every command is run as root through the installed launcher (§1); a command started without a terminal needs
`--instance <slug>`.

| Command | Purpose |
| --- | --- |
| `pf doctor` | Check the host tools, the control security, the Compose model, `.env` and the free space. |
| `pf status [--operation ID]` | Show the instance, the deployed revision, the workspace, the containers and the open operations; or one operation in detail. |
| `pf instances` | List the registered instances. |
| `pf ps [options] [SERVICE...]`, `pf logs [options] [SERVICE...]` | Read-only Compose views (§14). |
| `pf permissions check \| plan \| apply [--scope S]...` | Compare, preview or apply the permission policy (§2). |
| `pf permissions apply --resume \| --abandon` | Finish (`RESUME PERMISSIONS <slug>`) or undo (`ABANDON PERMISSIONS <slug>`) an interrupted apply. |
| `pf config admin`, `pf config app` | Create, migrate or complete `pf-config.json` or `.env` (§6). |
| `pf deploy [--latest \| --commit SHA \| --release TAG]` | First deployment of a new instance (§7). |
| `pf abort-deploy` | Remove an incomplete first deployment (`ABORT DEPLOY <project>`). |
| `pf update [--latest \| --commit SHA \| --release TAG] [--allow-migrations]` | Update to an exact revision (§9). |
| `pf backup [--emergency]` | Capture and restore-test a checkpoint, or preserve the data when a healthy checkpoint is refused (§10). |
| `pf backups [--page N]` | List the checkpoints. |
| `pf rollback [ID] [--restore-db]` | Roll back the code, or the code and the database (§10). |
| `pf reset-db` | Activate a clean migrated database and retain the previous one (§11). |
| `pf purge [--keep-backups \| --delete-backups] [--reset-admin-config]` | Save a verified recovery bundle, then remove the instance (§12). |
| `pf recoveries [--page N]` | List the purge recovery bundles. |
| `pf restore-instance ID [--side-by-side]` | Restore a purged instance, or restore a bundle into a kept recovery target beside it (§12). |
| `pf release-check [--channel C]` | Show whether an eligible release exists (§13). |
| `pf resume [--operation ID] [--abandon \| --keep-workspace \| --acknowledge]` | Continue, cancel or finish an operation, or acknowledge runner records (`RESUME`, `ABANDON`, `KEEP WORKSPACE` or `ACKNOWLEDGE <op8>`). |
| `pf cleanup [--apply] [--recovery-target P] [--checkpoint-history NAME] [--generation WSG]` | Report or remove recorded leftovers (§16). |
| `... deploy \| update \| rollback \| restore-instance --keep-workspace` | Run without the workspace switch (§8). |
| `sh ./deploy/synology/install-control.sh init --root <root>` | Create a new installation root (§5). |
| `pf install status` | List the install operations and their next step. |
| `pf install register ...`, `pf install migrate-legacy ...` | Register an instance (`REGISTER <slug>`), or register a v2.5 home (`MIGRATE <slug>`) (§5). |
| `pf install control --source DIR \| --release ID` | Install or select a control release (§15). |
| `pf install resume [--operation ID] [--abandon]` | Continue or undo an open install operation. |

## 18. Validation boundary

- The offline test suite (`python -B -m unittest discover -s deploy/synology/tests -p 'test*.py'`) simulates Docker and
  PostgreSQL and covers the controller logic, the file and archive handling, the permission policy, the journals and the
  purge and restore flows.
- On 2026-10-08 the lifecycle ran once with a real Docker Engine 28.5.1, Docker Compose v2.40.3 and PostgreSQL 16 inside
  an isolated, privileged Docker-in-Docker container on a generic Linux filesystem (the harness is in
  `deploy/synology/integration/`, the results are summarized in `TEST_REPORT.md`). The mandatory lifecycle loops, the
  side-by-side isolation and a crash matrix that interrupted every operation kind before and after its irreversible
  effects recovered with intact data, except for the defects that this run found; each of them failed closed without
  losing data. They were fixed afterwards with offline regression tests, and the fixed code has not been run again at
  the real level.
- Never tested: a real NAS, DSM, btrfs, SMB clients and DSM ACLs, a rootless Docker daemon, the real GitHub release
  source over HTTPS, power loss and host reboot.
- A refusal that comes after the contract probe of `update` (for example "Migration files differ" after the candidate
  build) still leaves the probe's image tags behind; `purge` removes them.
- Declared limits: a concurrent Docker or root administrator is outside every guarantee; `effective_access_verified` and
  `future_file_behavior_verified` are never verified; checksums prove integrity, not authorship; nothing runs unattended
  (§13); the Docker resources of a v2.5 installation are never adopted.

Before relying on recovery, run one disposable staging cycle interactively on the actual host:

```text
install-control.sh init
→ pf config admin --configuration <config> --project <project>
→ pf install register
→ pf config app
→ doctor → deploy --latest → backup → update → purge → restore-instance → check the user interface and the data
→ purge → deploy --latest → restore-instance <bundle> --side-by-side
```

`pf` is not a production tool. Production follows [`DEPLOYMENT.md`](../DEPLOYMENT.md).

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-11 | uncommitted | Rewritten as a short guide to what `pf` does today, with the section numbers that its messages cite kept; the stacked checkpoint notes, the v2.5 layout and the planned reusable-controller design were removed, and this table was added. |
| 2026-11-09 | `b405c6e` | Corrected the facts of the planned reusable-controller design. |
| 2026-10-14 | `2c360cb` | Recorded the planned reusable-controller design goal. |
| 2026-10-08 | `182b265`, `91e28d6` | Real Docker, Compose and PostgreSQL integration run (PF-A3.4) and the fixes of its findings. |
| 2026-10-08 | `3f0da91`, `1b44831`, `181a806` | Integrated operations (PF-A3.3): isolated bundle verification, side-by-side recovery targets, `pf cleanup` and the purged registry state. |
| 2026-10-07 | `38a40cf`, `d2d99bf` | Operation journal, `resume` and `--abandon`, and the workspace switch (PF-A3.2). |
| 2026-10-07 | `4d7fe76`, `af77172` | Deployment records, strict bundle manifests and emergency preservation (PF-A3.1). |
| 2026-10-07 | `2155c95`, `26b6ed4` | Semantic permission policy with `check`, `plan` and `apply` (PF-A2.3). |
| 2026-10-07 | `5d102d1`, `1244457` | Configuration wizards and the versioned administration settings (PF-A2.2). |
| 2026-10-07 | `964aefb`, `3b81f3a` | Installer with a preflight and journaled control releases (PF-A2.1). |
| 2026-10-06 | `4466ea8`, `53246a1`, `7d332e7`, `becd72a` | Daemon binding, Compose checks, exact resource inventory and removal of the Compose passthrough (PF-A1.3, PF-A1.4). |
| 2026-09-15 | `cef57ea`, `59d8cc0`, `13807ca` | Controlled process runner, frozen application configuration and protected source store (PF-A1.2). |
| 2026-09-14 | `6917c3f`, `5d59d2a`, `bc46deb` | Protected instance registration, registry, bootstrap verifier and locks (PF-A1.1). |
| 2026-09-13 | `1ce5e19` | Writable workspace separated from the privileged control (v2.5). |
| 2026-09-09 | `4f7ecd7`, `69c8664`, `e91adc3` | Guide created with updates, rollback, database reset and readable backups. |
