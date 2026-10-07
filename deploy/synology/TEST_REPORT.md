# PartFlow NAS Admin v2.5 — Validation report

Date: 2026-09-11. Tool version: 2.5.0.

> **PF-A1.1 checkpoint addendum (2026-09-15, revision r5 after audit-r2).** The v2.5 results
> below are historical. The current source carries the Deployment Admin PF-A1.1 slice
> (protected instance context, registry with durable reservations, bootstrap verifier run
> before any release code, canonical managed-path spelling and inventory, runtime
> daemon/project uniqueness, bootstrap-only installation root, stable locks, read-only
> construction/diagnostics that issue no transport when refused, legacy report that names
> but never reads the journal). Executed evidence lives in the PF-A1.1 checkpoint package
> (`IMPLEMENTATION_REPORT.md`, `AUDIT_FIX_MAP.json`, `ACCEPTANCE_RESULTS.json`, logs).
> Summary of that run, uid 0, Linux container, ext4 with POSIX ACL xattrs, group `users`
> present: full discovery `tests/` **191 tests OK, 0 skipped** on CPython 3.11.15, 3.12.3 and
> 3.13.13 (adapted baseline suite `tests/test_pf_admin.py` 100, PF-A1.1 suite
> `tests/test_instance_context.py` 91); the PF-A1.1 suite also 91 OK under CPython 3.9.23;
> the fifteen reviewer probes of audit-r1 stay 0/15 reproduced and the eight round-2 probes
> of audit-r2 reproduce 8/8 on the r4 source (`5d59d2a`) and 0/8 on this source in the same
> environment. Real DSM ACL/mount, Docker, PostgreSQL, DSM/SMB and power-loss durability were
> not exercised; A1-T17's host gate is therefore *blocked*, not passed.

> **PF-A1.2 checkpoint addendum (2026-09-15).** The source now carries the Deployment Admin
> PF-A1.2 slice: one controlled process runner (`pf_runner.py`: registered executables from
> `bootstrap/tools.conf`, allowlisted child environment, argv arrays, bounded and redacted
> capture, explicit deadlines, process-group termination, unresolved-effect records), strict
> application `.env` parsing with a private immutable per-operation snapshot and a
> percent-encoded `PARTFLOW_DATABASE_URL` (`pf_config.py`, `compose.nas.yaml`), and a
> protected Git source store with blob-level export plus fd-safe workspace/manifest comparison
> (`pf_source.py`); privileged Git no longer runs against `repo/`. Executed evidence lives in
> the PF-A1.2 checkpoint package. Summary of that run, uid 0, Linux container, ext4 with POSIX
> ACL xattrs: full discovery `tests/` **233 tests OK, 0 skipped** on CPython 3.11.15, 3.12.3 and
> 3.13.13 (adapted baseline suite `tests/test_pf_admin.py` 100, PF-A1.1 suite
> `tests/test_instance_context.py` 91, PF-A1.2 suite `tests/test_runner_config_source.py` 42);
> the PF-A1.1 + PF-A1.2 suites also 133 OK under CPython 3.9.23; installed-launcher evidence
> 55/55 scenarios (46 PF-A1.1 regressions kept, 9 PF-A1.2); the audit-r1 (15) and audit-r2 (8)
> probe runners stay 0 reproduced on this source; the protected store fetched commit
> `bc46deb` over HTTPS from the approved GitHub remote and exported 376 blobs byte-exact.
> Docker daemon/Compose execution, PostgreSQL, real DSM ACL/mount, SMB and power-loss
> durability were not exercised (registered fixture tools stand in for Docker; the real Git
> executable works against local and HTTPS remotes); A1-T17's host gate stays *blocked*.

> **PF-A1.2 r2 addendum (2026-09-15, audit A12-R01…R04).** Production wiring of the
> unresolved-effect journal: `compose()`, `docker()`, `sql()` and `store_git()` attach an effect
> descriptor (kind, verb, targets; no application values, redacted when persisted) to every
> mutating child and none to read-only ones, so a timeout/interruption of `up`/`down`/`stop`/
> `build`/`run`, `createdb`/`dropdb`/`pg_restore`, mutating SQL, Docker `tag`/`rm`/`image load`
> or a store `fetch` is journaled; A1-T09 now runs through the production wrappers without a
> hand-passed `effect=`; a mutating child outside a locked operation is refused before it
> starts (no journal to record into). Provenance: a commit that tracks `.env`, `node_modules`,
> `.venv`, `__pycache__` or `.pytest_cache` paths is refused before export, a candidate tree
> carrying one is refused before `repo/` is touched — in `rollback`/`restore-instance` before
> any confirmation, pause, safety snapshot or database swap — and a manifest listing one
> cannot be verified (A1-T10 extended, real-Git and no-Git). PostgreSQL client programs run
> as direct argv inside the `db` service (no `sh -c`; a static test forbids shell strings in
> every release module),
> and `re.split` uses `maxsplit=` (a static test compiles the release with
> `DeprecationWarning` as an error). Summary of the r2 run, uid 0, Linux container: full
> discovery `tests/` **240 tests OK, 0 skipped** on CPython 3.11.15, 3.12.3 and 3.13.13 (the
> 3.13 run also with `-W error::DeprecationWarning`); baseline suite 101, PF-A1.1 suite 91,
> PF-A1.2 suite 48; PF-A1.1 + PF-A1.2 suites 139 OK under CPython 3.9.23; installed-launcher
> evidence, audit probes and the HTTPS store probe rerun as recorded in the r2 checkpoint
> package. Host validations remain not exercised; A1-T17's host gate stays *blocked*.

> **PF-A1.2 r2 audit addendum (2026-10-06).** `SIGHUP` and `SIGQUIT` now unwind like `SIGTERM`/
> `SIGINT`: one handler (`install_interrupt_handlers`, installed by the entry point) interrupts
> the controller once and ignores repeats, so the runner terminates the child group, records the
> unresolved effect and `fail_closed()` runs before the instance lock is released; the effect is
> written before the streamed tail is flushed and `fail_closed()` runs even when the error report
> cannot be written (a hung-up terminal). Before this, a real `SIGHUP` killed the controller
> without unwinding: no effect, a still-running child, a free lock. `restore-instance` now
> extracts the bundle source, refuses reserved paths and runs the store's provenance proof
> before either RESTORE confirmation, the pending journal or any `config/.env` change, and
> `rollback` runs the provenance proof before confirmation, pause, safety snapshot or database
> swap; the r2 wording above was not yet true for `restore-instance`. New regressions (each
> failed on the r2 source): real `SIGHUP`/`SIGQUIT`/`SIGTERM`/`SIGINT` sent to the installed
> launcher during a mutating Compose passthrough (child group gone when the controller exits,
> one `interrupted` effect, lock free afterwards), effect recorded when the stream sink fails,
> `fail_closed()` with a failing stderr, handler registration, and the two restore-instance and
> one rollback ordering cases. Summary, uid 0, Linux container with real Git and `/tmp` on
> tmpfs: full discovery `tests/` **247 tests OK, 0 skipped** on CPython 3.12.15 and 3.9.25
> (baseline suite 106, PF-A1.1 suite 91, PF-A1.2 suite 50). Host validations remain not
> exercised; A1-T17's host gate stays *blocked*.

> **PF-A1.3 checkpoint addendum (2026-10-06) — Compose envelope, daemon binding, exact resource
> inventory.** New pure module `pf_docker.py` (release file; installed by both installer lists)
> and controller wiring: registration stores the resolved `unix://` socket and validation applies
> offline endpoint rules (a missing socket is a note); every process probes `docker info` once
> before any other Docker/Compose child and refuses drift, rootless, unusable or absent answers;
> `Controller.command()` takes `effect` as a required keyword, refuses `effect=None` on any argv
> the classifiers do not prove read-only (`unclassified-mutation`) and gates every Docker child on
> the daemon binding; every `up`/`run`/`build`/`create`/`start`/`restart` (managed and passthrough)
> first renders `compose … config --format json` with exactly its inputs (effective values and the
> `POSTGRES_DB=pf_migrate_*/pf_clean_*` overrides included) into a private `compose-<n>.json` and
> validates it against the topology allowlist with literal value comparison; `compose.nas.yaml`
> labels services, builds, the volume and the network with `DEPLOY_ADMIN_INSTANCE_ID`; an exact
> inventory (no prefixes, `Config.Env` never requested) classifies owned/excluded/blocker
> resources with their users; `deploy`/exact `restore-instance` require an empty target, guarded
> commands run an ownership preflight right after the lock, and `purge`/`abort-deploy` execute a
> frozen, hashed, closed plan with per-item and per-user reinspection (`compose down --volumes`
> removed from `abort-deploy`; images deleted only when their image ID is in the purge bundle).
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12`
> (CPython 3.12.15, git 2.47.3) and `python:3.9` (CPython 3.9.25, git 2.47.3) containers, uid 0,
> source mounted read-only and copied to `/tmp/r`: **326 tests OK, 0 skipped** on both (baseline
> suite `test_pf_admin.py` 107, PF-A1.1 suite 91, PF-A1.2 suite 50, PF-A1.3 suite
> `test_docker_scope.py` 78); no test failed or was skipped. `sh -n` passed for
> `pf.sh`, `install-control.sh`, `backup.sh` and `release-check.sh`, and every module and test
> file under `deploy/synology` parses with `ast.parse(..., feature_version=(3, 9))`. Docker is a registered fake (`tests/fake_docker.py`) or a
> fixture script; no daemon, NAS or running stack was contacted.
> Real-Compose render evidence (required, spec section 6.4): Docker Compose `v2.40.2-desktop.1`,
> harness host CLI (fallback source; no `docker:cli` image was cached and none was pulled),
> throwaway project `pfa13-fixture`, `config --format json` only. Escape mode **doubled**: every
> environment value equals `escape(sentinel)` for exactly one mode, including a password with `$`,
> `$$`, `"`, `'`, `\`, `#`, `@`, `%`, `&`, `<`, space and non-ASCII (the single quote went through the child environment with an empty
> env-file; the frozen env-file case used the same password without `'`, which the frozen format
> refuses); a second render with a planted
> `repo/compose.override.yaml` and a hostile `repo/.env` was byte-identical (modulo the project
> directory). The Windows CLI normalized `build.context` to a host path, so the
> `<repo_root>/<service>` rule is calibrated from the rule, not from this render (Linux render gap).
> Tokenized fixture: `fixtures/compose/partflow-2.40.2-desktop.1.json` (repository only).
> Gates: offline **passed** (A1-T18; A1-T16 with the stopped-daemon case); A1-T11…A1-T14 and
> A1-T17 stay **blocked** (Docker-daemon and NAS host gates `not_run`); A1-T15 `not_run` (PF-A1.4).
> Limits: a concurrent Docker or root administrator is outside the guarantee; a volume recreated
> within the same second with identical metadata is indistinguishable; the NAS Compose version is
> unverified; the rendered JSON is validated, not reused as the executed input; the Compose
> container-marker labels are not calibrated on a real daemon; image coverage by image ID assumes
> a quiescent daemon between `image save` and the binding inventory; passthrough CLI flags
> (`run -v`, `--cap-add`, `exec --privileged`) are not covered by the model envelope until PF-A1.4.
>
> **PF-A1.3 audit-fix addendum (2026-10-06).** An independent audit of `2d75f65..4466ea8` found
> six defects, fixed in the working tree with one regression test each (each new test was run
> against the `4466ea8` controller and failed there):
> (1) a blocker that appears in the binding inventory after `PURGE` was confirmed and services
> were stopped is now `plan-changed` with post-pause copy (no longer the pre-confirmation
> "Nothing was stopped" refusal); the application is reopened only when every new blocker is
> `resource-shared`, otherwise it stays stopped with the journal `paused` because Compose could
> adopt or recreate the resource (RI-21 blocker test);
> (2) passthrough `scale` and `watch` now pass the Compose envelope (`ENVELOPE_VERBS`; CE-6);
> (3) the `RESUME PURGE`/`RESUME ABORT DEPLOY` routes verify the daemon and the plan's engine before
> the prompt (DB-5 resume test);
> (4) an owned tag whose image ID a foreign container uses (created from the ID) is excluded
> `foreign-in-use` (inventory unit test and an RI-11 variant);
> (5) the per-item proof also requires a planned item that is still present to be classified
> owned, so a tag a foreign container starts using after the freeze stops with `plan-drift`
> (RI-11 late-user test);
> (6) a container that vanishes between `ps -a` and `container inspect` makes the inventory list
> again (3 attempts), then `inventory-unstable` (RI-25).
> Executed: the same command and images (CPython 3.12.15 and 3.9.25, uid 0, read-only source
> copied to `/tmp/r`): **332 tests OK, 0 skipped** on both (`test_docker_scope.py` 84); `sh -n` and
> the Python 3.9 `ast` parse of the 13 Python files under `deploy/synology` passed. The Docker-daemon
> and NAS host gates remain `not_run`.
>
> **PF-A1.4 checkpoint addendum (2026-10-06) — entry routes, no catch-all Compose; PF-A1 offline
> closure.** The Compose passthrough and every route to it are removed: each former word is a named
> refusal (`compose-route-removed` with the managed alternative), a leading Compose/Docker global
> option is `compose-override-refused`, any other leading option (abbreviations included; every
> parser sets `allow_abbrev=False`) is `unknown-option`, an unknown word is `unknown-command`, all
> before the registry is read, a lock is taken or a process starts. Explicit dispatch: `DISPATCH`
> (18 CLI routes: mutability, lock, trusted launch/context, pending route, preflight, fail-closed
> rule, unattended rule, policy class, handler) drives `main()`; `ENTRY_ROUTES` E1…E11 names every
> non-CLI route and its test. Read-only `pf ps` and `pf logs` rebuild the Compose argv from parsed
> options only (services `db`/`backend`/`frontend`; `--tail` 1–10000, default 200; `--since`/`--until`
> duration or RFC 3339; `logs -f` bounded by 3600 s and the runner's 64 MiB stream cap, ending with
> `logs-bound-reached`) and run through the validated context, runner and daemon binding without a
> lock or operation. Unattended gate (before the lock, stdin absent/closed/not a TTY):
> confirmation routes → `terminal-required`; `backup`, `permissions`, `release-check` →
> `instance-required-unattended` without `--instance`, then `policy-grant-required` (exit 20; the A1
> policy schema grants no class). `release-check --apply` → `auto-apply-not-permitted` (exit 20) with
> or without a terminal; `update(automatic=True)` keeps its guards (now unit-tested directly).
> Scheduler wrappers require `--instance`, use a fixed `PATH` and exec only the sibling
> `bootstrap/pf` or legacy `control/pf.sh` (whose report now names the command word after
> `--instance <id>`). `fail_closed()` stops every owned Compose one-off from the exact inventory
> (`pf_docker.owned_oneoffs`, sorted, a vanished one skipped with a warning, a cached daemon refusal
> ends it), then `compose stop frontend backend`; the legacy `partflow.admin.project` filter is gone
> and the copy names `pf --instance <slug> status`. `recoveries`/`restore-instance` list and verify
> only `<recovery>/<project>/` of the selected instance (`recovery-outside-instance`), `--project` is
> a validated name that must match `--instance` (`selection-conflict`), and a bundle may restore only
> `deployed.json`, `last-reset.json`, `observed-tags.json` (`recovery-state-file-refused`).
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12`
> (CPython 3.12.15, git 2.47.3) and `python:3.9` (CPython 3.9.25, git 2.47.3) containers, uid 0,
> source mounted read-only and copied to `/tmp/r`: **395 tests OK, 0 skipped** on both. Baseline
> (the 332 PF-A1.3 tests, reported separately): all pass; the classified conversions are
> `test_pf_admin` auto-update tests (direct `update(..., automatic=True)` calls),
> `test_destructive_compose_flags_are_rejected`, CE-6 (now `…former_passthrough_verbs_are_refused…`),
> RW-6, the retargeted signal test (`backup` at its first effect, `docker tag`, pty stdin), the raw
> `config`/`version` assertions, and the interactive harness (patched confirmation ⇒ simulated
> terminal) for the in-process and installed-launcher callers. New: 63 tests (`test_entry_routes.py`
> 62: DT 9 + E8, RC 5, RA 8, US 8, SW 6, EH 6, CI 6, SS 7, CLI 6; `test_pf_admin` 1). No test was
> skipped (the `pty unavailable` skip did not trigger). `sh -n` passed for `pf.sh`,
> `install-control.sh`, `backup.sh`, `release-check.sh`, and the 14 Python files under
> `deploy/synology` parse with `ast.parse(..., feature_version=(3, 9))`. Docker is a registered fake or
> a fixture script; no daemon, NAS or running stack was contacted.
> Gates (PF-A1 closure run): A1-T01…T10 **passed**; A1-T15 **passed** (cli_gate: CLI-1…CLI-3,
> CLI-7, RC, RA, SW-6); A1-T16 **passed** (cli_gate: CLI-4); A1-T18 **passed** (offline and CLI-5);
> A1-T11…T14 **blocked** (docker gate `not_run`; T11/T14 also need the real Compose `run` one-off
> label set that `fail_closed` relies on); A1-T17 **blocked** (host gate `not_run`). PF-A1 closure
> statement: every entry route uses the A1 primitives and no catch-all Compose route remains; the
> safety scope is proven offline only; no finding is closed overall and nothing is production-ready.
> Limits: PF-A1.3's (except the passthrough-flag line) plus terminal-based unattended detection; no
> unattended operation (scheduled backup and release check included) on a pf-managed instance until
> a PF-A4.3 policy grant (OD-A14-13); the wrappers are not installed by A1; `install-control.sh` is
> still the legacy installer; the real Compose one-off labels are unproven offline; no managed
> application-CLI route.
>
> **PF-A1.4 independent audit (fixes on 7d332e7).** Four minor findings, no blocker or major. Fixed
> with regression tests that fail on 7d332e7 and pass after: (1) `verify_recovery` refuses a
> manifest `state_files` that is not a list (`recovery-state-file-refused … lists state files as
> str|dict, not a list of file names`) instead of wrapping it, so the checked value is the value
> the restore consumes (CI-4 variant); (2) `recoveries()` uses the same lstat real-directory rule
> as `verify_recovery`, so a `purge-*` link to a bundle elsewhere is neither listed nor restored
> (CI-3 variant); (3) entry route E10 now names EH-7, a real SIGHUP/SIGQUIT/SIGTERM/SIGINT sent to
> the installed launcher during `resume` with an existing journal, which asserts the owned
> one-offs and then `compose stop frontend backend` are stopped while the instance lock is still
> held; DT-8 now requires the test of a `fail_closed` row to reach `fail_closed` (directly or
> through an `always` route); (4) the evidence package was refreshed (documentation only).
> Executed: the same discovery on `python:3.12` (CPython 3.12.15) and `python:3.9` (CPython
> 3.9.25), uid 0: **398 tests OK, 0 skipped** on both (395 + 3 new). Gate vocabulary (design r3
> WORK_PACKAGES §5): PF-A1 as a whole **PASS_WITH_DECLARED_LIMITS** (offline formal exit; docker gate
> A1-T11…T14 and host gate A1-T17 blocked; releases PF-A2.1 offline development only).

## Executed checks

| Check | Actual result |
| --- | --- |
| Offline unittest suite | **99 tests passed** |
| Python compilation of `deploy/synology/pf-admin.py` | **Passed with Python 3.13.5** |
| Python 3.9 grammar compatibility | **Passed** using `ast.parse(..., feature_version=(3, 9))` |
| Shell syntax: `pf.sh`, `install-control.sh`, `backup.sh`, `release-check.sh` | **Passed** with `sh -n` |
| External config + control Compose path construction | Covered by regression test |
| Writable repository / writable config permission policy | Exercised on real temporary filesystem |
| Read-only backup/recovery permission policy | Exercised on real temporary filesystem |
| Root-owned non-writable control-plane guard | Covered by regression test |
| Source archive / workspace archive / checksum operations | Exercised on real temporary files |
| Local Git checkout pinned to a non-tip SHA | Executed against a local temporary Git repository |
| Purge recovery bundle structure | Exercised with real filesystem/tar/checksum operations and simulated Docker/PostgreSQL |

Additional executed checks: `pf-config.example.json` parsed as JSON, `compose.nas.yaml` parsed
as YAML, the repository launcher refusal path passed, the simulated installed `control/pf.sh
--help` path passed, and both EN/VI guides were checked for the same 18 top-level sections.

## v2.5 coverage exercised

- Runtime split between `repo/`, `control/`, `config/`, `backups/`, `recovery/`, and `.pf-state-*`.
- `repo/` group-writable policy (`2770` directories, `0660` regular files) with setgid inheritance.
- `config/` group-writable policy and external `config/.env` / `config/pf-config.json`.
- `backups/` and `recovery/` group-readable/read-only policy (`0750` / `0640`).
- Repository `pf.sh` refusal and installed-control execution boundary.
- Root-owned/non-group-writable control-plane validation.
- Controller Compose invocation with explicit `--env-file`, `--project-directory`, installed control Compose file, and `PARTFLOW_REPO_ROOT`.
- First-run environment creation outside the repository with a cryptographically random PostgreSQL password.
- Exact deployed revision stored in external state, separate from editable workspace HEAD/dirty state.
- Manual update preservation of a dirty/different workspace as `workspace.tar.gz` before repository replacement.
- Automatic update refusal when the editable workspace differs from the deployed revision.
- Full repository replacement without preserving privileged runtime files inside the repository.
- Revision checkpoints with exact deployed source plus optional editable-workspace archive.
- Multi-instance selection, purge confirmation gates, recoverable purge bundle, interrupted purge resume, exact restore, and side-by-side DB recovery.
- Purge recovery preservation of external `.env`, admin config snapshot, editable workspace archive, database payloads, application images, checkpoint history and state metadata.
- Recovery keeps the currently installed control plane while restoring repository/runtime state.
- Explicit refusal to generic-merge recovered production history into a newer active database.
- Static installer coverage for legacy `.env`/`pf-config.json` migration, conflicting-config refusal, external layout, read-only control permissions, and root-only launcher.

Existing update, backup, migration rehearsal, rollback, reset, release selection, CI gating,
checksum, archive-safety, locking and production-guard regression coverage remains in the
same suite.

## Not executed / not certified

Docker and PostgreSQL interactions are simulated in this environment. No actual Synology
DSM, Container Manager, Docker daemon, PostgreSQL 16 instance, SMB ACL stack, reverse proxy,
or live PartFlow application container was available for integration testing.

In particular, the following must still be rehearsed on a disposable NAS staging instance:

- `install-control.sh` ownership/mode behavior under DSM.
- `docker compose --env-file` and `--project-directory` behavior with the NAS's installed Compose version.
- Build contexts that use external `PARTFLOW_REPO_ROOT`.
- SMB create/edit/delete behavior in `repo/` and `config/`, including DSM Shared Folder ACLs.
- Read-only SMB behavior for `backups/` and `recovery/`.
- Real PostgreSQL dump/restore, migration rehearsal, database rename/cutover, purge and recovery.
- Docker image save/load during full purge recovery.
- Actual application health and shop-floor workflow behavior after update/recovery.

These limitations are intentional and must not be described as passed runtime validation.
