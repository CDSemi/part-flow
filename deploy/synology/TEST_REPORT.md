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

> **PF-A2.1 checkpoint addendum (2026-10-07) — preflight installer, staged control generations,
> journaled switch.** New module `pf_install.py` (required release file) with the wire schema
> `contracts/install-operation.schema.json` (embedded copy compared by a test): `install-control.sh` is
> now a thin `init`-only wrapper that execs the repository `pf_install.py` with a root-owned isolated
> interpreter; `pf install status | register | migrate-legacy | control | resume` run from the installed
> control (`DISPATCH` row `install`, 19 routes; `ENTRY_ROUTES` E4 = the global launcher created only when
> absent, E11 = `installer-init`). Every kind prompts missing inputs per stage, runs a read-only preflight
> that reports every conflict at once, shows the frozen plan and asks a typed phrase; then it takes its
> locks non-blocking (init: flock on a private sibling build directory; registry lock, then the v2.5
> `operation.lock` or every instance lock in UUID order), re-runs the preflight under them
> (`install-plan-changed`), publishes `plan.json` + `journal.json` as one atomic intent and journals each
> effect `intended` (with the created object's identity) and `complete`. `resume` observes every effect
> before deciding (`not_started`/`complete`/`partial`/`unknown`); `unknown` → `needs_operator` (open,
> gating, with its next step); `rolling_back`/`abandoning` resume to their end; `--abandon` restores the
> previous binding or discards a never-used registration (`pf_instance.discard_unused_registration`).
> `control` stages a content-addressed release (`r-<16 hex>` of the release-id-free content inventory),
> smoke-checks it with the registered interpreter (`pf_runner` tool id `interpreter`; cwd outside the
> release; the live `pf-config.json`/`.env` of every instance against a running-release baseline),
> re-verifies, publishes, binds `bootstrap.conf` then every record, verifies end to end through the real
> bootstrap and restores automatically on failure; the bootstrap bytes stay frozen
> (`bootstrap-change-unsupported`). `migrate-legacy` copies legacy-only files byte for byte through a
> staged `O_EXCL` name and a no-clobber `os.link`, registers with the user's paths and leaves the v2.5
> `control/` active (`legacy-control-active` in `Controller.lock`, together with the binding re-check
> `control-binding-changed` and the gate `install-operation-pending`). A1 changes: `register_instance(...,
> registry_lock=)`, `initialize_installation_root(build_dir=, wrappers=)`, the root requires
> `install-operations/` and `home/.docker/config.json`, `pf_config.validate_admin_config` (the extracted
> `load_app_config` rules, messages unchanged), the interrupt handler moved to `pf_runner` (re-exported),
> `CHECKPOINT = "PF-A2.1"`.
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (CPython
> 3.12.15, git 2.47.3) and `python:3.9` (CPython 3.9.25, git 2.47.3) containers, uid 0, source mounted
> read-only and copied to `/tmp/r`: **463 tests OK, 0 skipped** on both. Baseline (the 398 PF-A1 tests,
> reported separately): all pass; converted: `test_control_installer_encodes_external_layout_…` →
> `test_control_installer_is_the_thin_init_only_wrapper` (the v2.5 copy installer is replaced), DT-1/DT-2/
> DT-4/DT-8/DT-9, SS-1/SS-3/SS-5/SS-7, SW-6 (wrappers placed by the installer), RW-1/RW-6 and the
> installer-text assertions of `test_runner_config_source`. New: 65 tests (`test_install.py` 55: CS 3, PF 9,
> RI 4, LB 3, CN 4, RS 4, CM 8, SM 2, IP 1, CC 7, LG 3, SC 3, LM 3, Schema 1; `test_entry_routes` 2: DT-2b,
> SS-3b; `test_instance_context` 5; `test_runner_config_source` 3). Crash matrix (`INSTALL_CRASH_MATRIX.json`):
> 270 forked rows (control 108, control rollback 14, migrate-legacy 108, init 36, register 4; every labelled
> boundary before/after each journal write and each effect, resumed through the installed CLI with
> `resume` and `resume --abandon`, or a re-run of init before publication) and 8 real-signal rows
> (SIGINT/SIGHUP/SIGTERM during the control and init smoke, SIGKILL during verify and during the init
> smoke); every outcome is terminal (completed, cancelled, abandoned, rolled_back or no operation) with
> exactly one bound generation (control/migrate-legacy/register rows: old release retained, configuration
> bytes unchanged; init rows have neither); the only Docker argv in any row is the read-only `docker info` of the
> registration preflight (migrate-legacy and register rows). `sh -n` passed for `pf.sh`, `install-control.sh`, `backup.sh`, `release-check.sh`; the 16 Python
> files under `deploy/synology` parse with `ast.parse(..., feature_version=(3, 9))`. Docker is the
> registered fake; no daemon, NAS, `/usr/local/bin/pf` or running stack was contacted.
> Gates: A2-T01, A2-T02, A2-T03 **passed** (offline, installer); A2-T04 **not_run** (needs a real Docker
> daemon; IP-1 offline identities passed; owners PF-A3.4/PF-A5.1); the real global launcher and reboot/power
> loss **not_run** (PF-A5.1); A1-T01…T10, T15, T16, T18 **passed** (re-run); A1-T11…T14 and A1-T17 stay
> **blocked**. Limits: offline only; one control release per root; launcher/verifier bytes frozen until
> PF-A4.3; no legacy resource adoption, v2.5 control retirement or state import (OD-A21-05); no
> configuration creation (PF-A2.2); no permission change (PF-A2.3); no cleanup of old releases (PF-A5.1);
> no durability claim beyond fsync and rename on the tested filesystem.

> **PF-A2.1 audit addendum (2026-10-07, uncommitted over `964aefb`).** The implementation audit
> (`becd72a..964aefb`, 18 confirmed findings, all minor after verification) is closed with regression tests
> that fail on `964aefb` (16 of 16, run against its `pf_install.py`, `pf-admin.py` and schema) and pass after
> (`test_install.AuditRegressions` AU-1…AU-16): (1) the init re-check under the locks scans leftovers again, so
> a concurrently confirmed init is refused (`install-busy` → `install-plan-changed`); (2) a failed or
> interrupted root rename is before the commit point (build removed, re-run `install-control.sh init`, never a
> `resume` of a root that does not exist), and an empty root that is a mount point or another device is
> `root-exists`; (3) an own build holding only an empty `install-operations/` is an own leftover; (4)
> `--source`/`--release` keep the typed flag; (5) `control-unchanged` names a differing scheduler wrapper (a
> wrapper-only change is installed with the next release change); (6) an interrupt after the intent rename
> cancels the open operation; (7) the `.env` hash never reaches stderr, stdout or the journal; (8) a launcher
> created by another writer before resume is left (`launcher-left`); (9) the hard-linked `.pf.tmp-<op8>` is
> removed by resume and abandon; (10) an unreadable `install-operations/` entry gives
> `install-operation-unreadable` and a manual next step; (11) legacy and editor-writable files are read
> non-blocking, regular and bounded (a FIFO never stalls the installer); (12) an invalid admin config no longer
> hides the launcher, free-space and v2.5 state checks (note `legacy-state-unchecked`); (13) an interrupted
> automatic restore or cancel cleanup is `install-interrupted`, and a cancelled release leaves `releases/`
> by one rename before its removal; (14) the unused `import signal` is gone from `pf-admin.py` (AU-16 checks
> both entry modules). The SS-3/SS-3b allowlists gain the two reviewed write sites (`os.rename` in
> `_Run.cancel`, `os.unlink` in `_Run._remove_own_launcher_temp`). The schema's top-level description now
> states that its `description` markers are normative and enforced only by `pf_install.validate_document`.
> Executed: the same discovery on `python:3.12` (CPython 3.12.15) and `python:3.9` (CPython 3.9.25), uid 0:
> **479 tests OK, 0 skipped** on both (463 + 16); `sh -n` for the 4 scripts, `ast` (3, 9) for the 16 Python
> files and the strict JSON load of the schema passed in both runs. Gate statuses are unchanged (A2-T01…T03
> passed offline; A2-T04, the real global launcher and reboot/power loss not_run; A1-T11…T14, A1-T17 blocked).

> **PF-A2.2 checkpoint addendum (2026-10-07) — config wizard and schema migration.** Wire schemas first:
> `contracts/admin-config.schema.json` (admin config schema 2, flat, A1 subset keywords) and
> `contracts/config-change.schema.json` (audit record, A2.1 description markers incl. the new target `scalar`),
> each equal to its embedded copy in `pf_config.py`; examples under `contracts/examples/admin-config/` bound to
> their outcomes by `cases.json`; the shipped `pf-config.example.json` gains `"schema_version": 2` only, and the
> `nas.env.example` header names `pf config app`. `pf_config.parse_admin_config` reads legacy schema 1 (no
> `schema_version`, frozen implicit values, the A1 messages unchanged) and schema 2 (every key explicit); any
> other version is `admin-config-version-unsupported`; `validate_admin_config` keeps the A2.1 smoke shape.
> Explicit migration (`migrate_admin_config`) keeps explicit values and materializes the frozen schema 1 values;
> loading never migrates. New CLI group `pf config admin|app` (DISPATCH row `config`, 20 routes; `pf config` alone
> or with any other word stays `compose-route-removed`); `main()` skips only the configuration load and the
> snapshot freeze for `config`; a pre-registration mode `pf config admin --configuration DIR [--project P]`
> takes only the registry lock. The writer (`write_editable_file`) refuses symlinks, special or hard-linked files,
> ACL-bearing files and unexplained reserved temp names before the first question, then compares-and-swaps
> (rename for replace, no-clobber link for create) and removes classified crash leftovers; registered-mode writes
> record `config-change.json`. `pf config app` plans per declared key of the record's profile
> (`APP_DECLARATIONS`), keeps secrets byte for byte, generates a password only for a never-deployed instance
> (no `deployed.json`, no completed `migrate-legacy` pinned to it, readable state), never rewrites credentials
> once deployed, and checks new timezones against the interpreter's compile-time TZPATH (never `PYTHONTZPATH`);
> `deploy`'s missing-`.env` branch uses the same wizard (`render_env_template` removed). `pf_install`:
> `validate_marked` (the marker walker parameterized by `$defs`; `validate_document` unchanged in behaviour),
> `read_regular_file` alias, per-verb copy for `admin-config-missing`, `group-missing` and `app-env-unparsed`.
> `pf_instance.POLICY_SCHEMA_VERSION` with its own message. `doctor` adds the schema and host zone data lines.
> `CHECKPOINT = "PF-A2.2"`. `pf_bootstrap.py`, `pf.sh`, `INSTALL_CONTRACT`/`INSTALL_SCHEMA_VERSION` and the install
> schema are byte-identical to `3b81f3a` (BF-1).
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (CPython 3.12.15)
> and `python:3.9` (CPython 3.9.25) containers, uid 0, source mounted read-only and copied to `/tmp/r`:
> **556 tests OK, 0 skipped** on both. Baseline: the 479 PF-A2.1 tests minus one moved test (478) all pass;
> updated without weakening: DT-1 (20 routes), DT-7 (`config` leaves the removed-word table, its guidance stays),
> DT-9 (`config` confirms with `[y/N]`, OD-A22-17), SS-2 (registry reload of the pre-registration mode), SS-3
> (writer sites), SS-5/RW-6 (checkpoint), CE-5 (the new `Timezone data:` line is checked separately from the
> password scan), the two `prepare_new_env` tests (never-deployed fixture; port asked before the hostname, the
> declaration order), and `test_render_env_template_rejects_missing_required_sample_field` → ER-3. New: 78 tests
> (`test_config.py` 72: SC-1/SC-2/BF-1 and the shipped example 4, EX-1 1, AV 4, AM 2, AW 12, PR 8, AD 2, AP-1…AP-11
> plus the wizard part of ER-3 12, ZD 4, ER-1…ER-3/UR-1 4, CC 4, CR 3, SK 2, SC-3 1, PO 1, LC-1 1 plus 7 schema 2
> re-runs of A1 deploy/update/backup/rollback/purge/side-by-side restore tests; `test_entry_routes` 4: DT-3, DT-5,
> DT-5b, SS-3c; `test_install` 2: per-verb copy). Evidence outside the suite: UP-1 (A2.1 `install-control.sh init`
> from `git archive 3b81f3a`, register with a schema 1 file, `install control --source` the A2.2 tree → config
> bytes unchanged, `config admin` → schema 2, `install control --release <A2.1>` → `install-smoke-failed`, nothing
> bound: PASS) and SECRET-SCAN (every file under the app-wizard test roots scanned for every test secret, raw and
> percent-encoded: 0 hits outside `.env`). `sh -n` for the 4 scripts, `ast` (3, 9) for the 17 Python files and the
> strict JSON load of the 3 contracts passed. Docker is the registered fake; no daemon, NAS, `/usr/local/bin/pf`,
> running stack or development database was contacted.
> Gates: A2-T05 **passed** (offline); A2-T06 **passed** (offline, OPS scope: host zone data, secret preservation,
> generation only when required, OPS URL encoding) with two declared limits: the backend image's zone data is
> unchecked (owner unassigned, OD-A22-21) and app-side consumption of an encoded URL is **pending the app-lane
> `backend/alembic/env.py` fix** (WP-APP not built; backend gate not run); A1-T08 **passed** (re-run + ER/UR);
> A2-T01…T03 **passed** (re-run); A2-T04, the global launcher and reboot **not_run**; container zone data, DSM
> ACL-bearing files and the SMB write race **not_run**; A1-T11…T14, A1-T17 stay **blocked**. Limits: a password
> that needs URL encoding fails at the backend migration step until the `env.py` escape lands; ACL-bearing config
> files are refused (manual edits until PF-A2.3); a `backup_read_group` change takes effect without
> revision-bound approval (OD-A22-20); offline only.

> **PF-A2.2 audit addendum (2026-10-07, uncommitted over `5d102d1`).** The implementation audit
> (`3b81f3a..5d102d1`, 12 confirmed findings, all minor after verification; `_claude_outputs/ops/PF-A2.2/
> audit-findings.json`) is closed with `test_config.AuditRegressions` AU-1…AU-9. Seven of them fail on the
> `5d102d1` `pf-admin.py`/`pf_config.py`/`pf_install.py` (20 subtest failures) and pass after; AU-4 and AU-9
> add the missing evidence for paths that already behaved. Fixes: (1) pre-registration `pf config admin
> --configuration` also runs the A2.1 registry checks, so a registered record that cannot be loaded refuses
> (`registry-record-invalid`) before any question and again under the registry lock (AU-1); (2) when the access
> mode is asked and the DSM Reverse Proxy is chosen, a kept `PARTFLOW_ALLOWED_HOST=localhost` is asked with no
> default (AU-2); (3) an interrupt or I/O error while `config-change.json` is written after the publish is
> `config-audit-not-recorded`, reported from an observation of the target (AU-3); (4) the writer's interrupt
> mapping (`config-cancelled` before the publish, `config-interrupted` after it) now has tests for fchmod,
> rename, link and the directory fsync, and `config-interrupted` is documented (AU-4); (5) the pre-registration
> `admin-config-invalid` copy repeats `--configuration`/`--project` (AU-5); (6) the `install-control.sh init`
> completion names `config admin --configuration <config> --project <project>` (AU-6); (7) a present
> `SITE_TIMEZONE` with a `.`/`..` component (`zone_status` `invalid-name`) is asked instead of kept (AU-7);
> (8) `.env` parser messages never echo a value character (unsupported escape, invalid UTF-8 byte, control
> character), in `app-config-invalid` and in the register note `app-env-unparsed` (AU-8); (9) Direct LAN through
> `pf config app` is covered and the test module docstring corrected (AU-9); (10) SYNOLOGY_ADMIN (EN/VI) states
> that the wizard inside `deploy` writes no `config-change.json`. The PF-A2.1 SS-3/SS-3b allowlist entries
> (`os.rename` in `_Run.cancel`, `os.unlink` in `_Run._remove_own_launcher_temp`) and the PF-A2.2 entries
> (`os.replace`/`os.link`/`os.unlink`/`fchown`/`fchmod`/exclusive create in `write_editable_file`, `os.unlink`
> in `remove_editable_leftovers`, `write_private_json` in `Controller.write_config_change`) were re-reviewed:
> each acts on an own temp, a classified leftover or the reviewed target after a fresh compare, and no fix adds
> a write site. OD-A22-20 and OD-A22-21 are unchanged. Executed on the final tree: the same discovery in
> `python:3.12` (CPython 3.12.15) and `python:3.9` (CPython 3.9.25), uid 0: **565 tests OK, 0 skipped** on both
> (556 + 9); SECRET-SCAN with the AU cases (20 cases, 12 tracked secrets, 0 hits outside `.env`); `sh -n` (4
> scripts), `ast` (3, 9) (17 files) and the strict JSON load of the 3 contracts passed in both images;
> `pf_bootstrap.py` and `pf.sh` hashes unchanged (BF-1). Gate statuses are unchanged.

> **PF-A2.3 checkpoint addendum (2026-10-07) — semantic permission policy, check/plan/apply; PF-A2 offline gate.**
> Wire schemas first: `contracts/permission-policy.schema.json` (byte copy of the r2 schema, sha256 `578b31a9…c8d4`),
> `contracts/permission-approval.schema.json` and `contracts/permission-apply.schema.json`, each equal to its
> embedded copy in `pf_config.py`; the r2 examples (byte copies) and the invalid corpus under
> `contracts/examples/permission-policy/`, bound to their outcomes by `cases.json`. `pf_config` parses strictly (the
> A1 subset of the inlined r2 schema, plus its removed `allOf` as a semantic rule), compiles the PERMISSIONS section 2
> mode table and the section 3 scope floors, refuses to activate workspace `executables: none` (OD-A23-18), derives
> the unapproved policy with the backups/recovery groups of their folder gids (OD-A23-02) and validates the approval
> chain. `pf_instance` adds the fd-relative, no-follow, bounded scope inventory (links, special files, hard links,
> mount boundaries, `@docker` and daemon roots, untrusted owners in protected scopes, access or unknown ACLs and the
> entry limit are blockers), the descriptor ACL classifier (agrees with `inspect_posix_acl`), the metadata engine
> (identity, link count, before state and ACL re-checked; fchown, then fchmod, then fstat) and the `/proc` open-handle
> scan. `pf-admin.py`: `pf permissions check|plan` (read-only, no lock, refuse findings mapped onto scopes),
> `pf permissions apply` (wizard, `APPLY PERMISSIONS <slug>`, revalidation, persisted intent, fence-first editor
> freeze, write-ahead effect journal, authoritative inventory of fenced scopes, verification, then the record
> `<private_state>/permission-policy.json`), `--resume`/`--abandon`, the `permissions apply` journal route, the doctor
> line, the admin-wizard proposal lines, content-only copies (`copy_fresh`) and explicit targets (`publish_fresh`,
> `apply_single`) in every lifecycle flow; `deploy --current` no longer changes the workspace; bare `pf permissions` is
> `permissions-verb-required` (exit 2); `CHECKPOINT = "PF-A2.3"`. `pf_bootstrap.py`, `pf.sh`, `pf_install.py`,
> `pf_source.py`, `pf_runner.py`, `pf_docker.py`, `compose.nas.yaml` and the four shell scripts are unchanged (BF-1:
> `84a824c8…0281`, `aadc41db…3532`).
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (CPython 3.12.15,
> git 2.47.3) and `python:3.9` (CPython 3.9.25, git 2.47.3) containers, uid 0, source mounted read-only and copied to
> `/tmp/r`: **650 tests OK, 0 skipped** on both. Baseline: the 565 PF-A2.2 tests pass, updated without weakening per
> SPEC section 6.2 (DT-1/2/3/5/5b/9, US-1/US-7, SS-3/SS-3c, SS-5/RW-6, the two `test_pf_admin` permission tests, the
> `test_instance_context` mutation, diagnostic, hard-link and launcher tests, three `test_runner_config_source` tests,
> AW-A9 and LC-1). New: 85 tests in `test_permissions.py` (Contracts 6, Compiler 11, Approval 11, Inventory 12,
> ContextMapping 2, Acl 5, Freeze 8, FutureFiles 4, Partial 11, Routes 8, Flows 7; some test methods cover two case
> IDs). No skip: the POSIX ACL xattr fixtures and the forked unprivileged identities (uid 4242/4343 with the image's
> `users`/`staff` groups) ran. Evidence outside the suite: PERM-FS-1 (installed launcher check → plan --details →
> apply → check with `stat` listings, unprivileged probes, an editor working directory refusing the freeze, an
> interrupted apply blocking `backup`, abandon and resume, a symlinked workspace root reported as
> `scope-path-unsafe`, purge keeping the record), PERMISSION_COMPILATION_RESULTS.json (every table row, executable
> combination, scope and example; equal to `cases.json`), SECRET-SCAN (the PF-A2.2 cases plus FF/FL/PA/AR-10: 0 hits
> outside `.env`), `sh -n` (4 scripts), `ast` (3, 9) (18 files) and strict JSON of the 7 contracts and the examples
> (the duplicate-key example refused as intended) in both images. No daemon, NAS, `/usr/local/bin/pf`, running stack
> or development database was contacted.
> Gates: A2-T07 **passed** (offline); A2-T08 **passed** (filesystem) with declared limits (residual hard-link race
> window; custom daemon roots on the same device not detected offline); A2-T09 **blocked** (host gate): filesystem
> part passed, SMB/DSM part `not_run` (PF-A5.1); A2-T10 **passed** (filesystem); A1-T17 stays **blocked** (host gate),
> offline part re-run passed; A2-T01…T03, T05, T06 and A1-T08 **passed** (re-run); A2-T04, the global launcher,
> reboot/power loss, DSM ACL-bearing files, the SMB write race and container zone data stay `not_run`; A1-T11…T14 stay
> **blocked**. Declared limits: no DSM/SMB effective-access or future-file claim; ACL'd scopes cannot be bulk-applied
> and fresh entries that inherit an ACL stop protected flows; the freeze's residual window for root services and
> memory maps; custom daemon roots on the same device; until the first approval `workspace_write_group` still sets the
> group of files pf creates in the editable scopes; workspace `executables: none` is not activatable; the control
> release is checked, never changed, fixed at No group access; ACL-bearing config files stay refused (OD-A23-06); an
> invalid approval record has a manual route only.
> **PF-A2 offline gate.** Present and green: the installer crash matrix, cancellation and restart (PF-A2.1,
> A2-T01…T03); resource identities and config/secret preservation (PF-A2.1/A2.2: UP-1, SECRET-SCAN, A2-T06); schema
> migration (PF-A2.2: CONFIG_MIGRATION_RESULTS.json, A2-T05); permission compilation and real-filesystem evidence
> (PF-A2.3: PERMISSION_COMPILATION_RESULTS.json, PERM-FS-1, A2-T07/T08/T10, A2-T09 filesystem part); the English
> operator guide and its Vietnamese translation (SYNOLOGY_ADMIN.md/.vi.md). No runtime or host-level success is inferred
> from a schema pass. Recommended PF-A2 verdict: `PASS_WITH_DECLARED_LIMITS` (offline), releasing PF-A3.1 offline
> development. Host-gated cases still open: A2-T04, the A2-T09 SMB/DSM part, the A1-T17 DSM ACL/mount part, the global
> launcher, reboot/power loss, DSM ACL-bearing config files, the SMB write race and A1-T11…T14 (owners: PF-A2.1 re-run
> on a host, PF-A3.4, PF-A5.1); container zone data stays unassigned (OD-A22-21). Not production-ready; F03, F08, F09,
> F11, F12 and F14 are not closed overall.

> **PF-A2.3 audit addendum (2026-10-07).** The PF-A2.3 package was committed as `2155c95`; its 650-test runs were taken
> from that tree. The independent audit confirmed 9 distinct findings (none refuted) and fixed them in the OPS paths:
> (1) the editor-freeze open-handle scan now runs after the authoritative inventory of the fenced scopes, over the
> union of plan-time and authoritative identities, so a cwd/fd holder on an entry created between plan and fence
> refuses the apply (FZ-9, FZ-10); (2) `copy_fresh` is descriptor-relative and no-follow on both sides, refuses
> hard-linked sources and never reads or writes through an entry swapped after its check (FL-11..FL-15); (3) a missing
> or unsafe backups/recovery root on an unapproved instance blocks only that scope in `check`/`plan` ("group
> unavailable", FS-7b); (4) a refuse finding at or above the installation root, private state or the control release
> is context-level even when shared with a data root (RT-9b); (5) SS-3 tracks `ftruncate`/`truncate` and write-mode
> `os.open`, and the resume truncation is pinned (SS-3d); (6) the walk classifies a directory swapped for a link
> between lstat and open as `scope-entry-link` (FS-13b, FS-13c); (7) SYNOLOGY_ADMIN(.vi) §2/§3/§16 updated, including
> eight missing permission codes. Executed on `2155c95` plus these fixes, disposable `python:3.12` (3.12.15) and
> `python:3.9` (3.9.25) containers, uid 0: **662 tests OK, 0 skipped** on both; the new regressions fail on `2155c95`
> (FS-13c and FL-15 are coverage only). Static checks unchanged; `pf_bootstrap.py` and `pf.sh` byte-identical (BF-1).
> A2-T08 is restated **passed** (filesystem) on the FZ-1..FZ-10 and FL-11..FL-14 evidence with the same declared
> limits (residual hard-link race window; custom daemon roots on the same device). Recommended PF-A2 verdict unchanged:
> `PASS_WITH_DECLARED_LIMITS` (offline). Nothing was run against a NAS, DSM, SMB client, Docker daemon or the running
> stack.

> **PF-A3.1 checkpoint addendum (2026-10-07) — deployed artifacts, lifecycle schemas, emergency preservation.**
> Contracts first: `contracts/lifecycle-records.schema.json` (five frozen records — `operation_plan`,
> `operation_journal`, `deployment_record`, `recovery_manifest`, `verification_record` — with shared `$defs`), equal
> to `pf_config.LIFECYCLE_SCHEMA`, with valid examples, legacy format 1/2 inputs and their migrated outputs, an invalid
> corpus and `cases.json` (54 rows) under `contracts/examples/lifecycle/`. `pf_config` adds the cross-field validators
> and the pure, deterministic `migrate_legacy_manifest`; `pf_source` the fd-safe archive writer, the streaming
> inspector and the descriptor-relative exclusive extractor (`ArchiveLimits`); `pf_instance` `publish_private_dir` and
> `remove_private_tree_at`. `pf-admin.py`: strict bundle reader (exact manifest bytes, payload size/hash/no-follow/
> single link, unlisted files refused) before any extraction, confirmation or journal; the deployed artifact store
> (preflight before the confirmation, staging after it and before the first effect, seal after activation, pointer
> keys, non-wedging seal failure); healthy/emergency/partial captures with deployment image binding; `preserve_current`
> in every rollback (`preservation-failed`); verification records outside the bundle; PostgreSQL facts, connection
> window and locale-aware candidates; purge bundle schema 1 with the db image saved by ID and its own-payload restore
> verification gating deletion (`purge-bundle-unverified`); restore-instance from the source payload; `pf backup
> --emergency`; status/doctor `Deployment:` lines; `CHECKPOINT = "PF-A3.1"`. `pf_bootstrap.py`, `pf.sh`,
> `pf_install.py`, `pf_runner.py`, `pf_docker.py`, `compose.nas.yaml` and the shell wrappers are unchanged (BF-1:
> `84a824c8…0281`, `aadc41db…3532`).
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (CPython 3.12.15,
> git 2.47.3) and `python:3.9` (CPython 3.9.25, git 2.47.3) containers, uid 0, source mounted read-only and copied to `/tmp/r`:
> **771 tests OK, 0 skipped** on both (1088.240 s and 1086.951 s). Baseline: the 662 PF-A2.3 tests pass, updated without
> weakening per SPEC section 6.2 (`test_pf_admin`, `test_docker_scope`, `test_entry_routes` incl. the SS-3 allowlist
> reclassified with reasons and 23 DISPATCH rows, `test_permissions`, `test_config`, and the real-signal backup test of
> `test_runner_config_source`, which now records the deployed workspace manifest because `backup` proves the source in
> its preflight). New: 109 tests — 108 in `test_artifacts.py` (Contracts 6, Pinning 1, ManifestStrict 12, Legacy 9,
> ArchiveImport 17, ArchiveRoundTrip 6, DeployedArtifact 19, Emergency 11, Verification 4, Postgres 8, PurgeBundle 10,
> Routes 5; some methods cover two case IDs) and one purge deletion-gate regression in `test_pf_admin.py`. Evidence
> outside the suite: ARCHIVE-FS-1 (every AX/AR archive imported as uid 0 on a real filesystem: 39 imports, 37
> refusals, sentinel and destination parent unchanged after each), ARTIFACT-FS-1 (simulated daemon: deploy → delete
> `repo/` and `.git` → backup → rollback → update; the checkpoint and rollback deployment carry the byte-identical
> source archive of the first deployment; record and pointer hashes agree), DOWNGRADE-1 (the A2.3 listing of `26b6ed4`
> raises `KeyError: 'id'` on a schema-1 checkpoint, as declared), SCHEMA-CASES (54/54 rows hold; legacy pairs
> byte-equal), static checks in both images (`ast` (3, 9) on 19 files, `sh -n` on 4 scripts, strict JSON of 86
> contract files with the three intended invalid examples refused, schema equality, BF-1) and a secret scan of the
> logs and the checkpoint package. No daemon, NAS, `/usr/local/bin/pf`, running stack or development database was
> contacted.
> Gates: A3-T01 **passed** (offline); A3-T02 **passed** (filesystem, simulated daemon); A3-T13 **passed**
> (filesystem); A3-T03 **blocked**: its offline part passed (EP-*), its required `docker_postgresql` level is not run
> (PF-A3.4). Earlier A1/A2 offline cases re-run **passed**; A2-T04, the A2-T09 SMB part, the A1-T17 DSM part,
> A1-T11…T14, the launcher and reboot/power loss stay `not_run`/`blocked`. Declared limits: no real Docker, Compose or
> PostgreSQL claim; no `functional_recovery_verified` writer (purge deletion is gated on the bundle's own
> `data_restore_verified` record; PF-A3.3); emergency preservation only in `rollback` and `backup --emergency`;
> unsealed staging and superseded deployments are never cleaned (PF-A3.2/PF-A5.1); archived db layers unused at
> restore (PF-A3.3); downgrade to the A2.3 control unsupported while schema-1 bundles exist; checksums are integrity,
> not authorship. Recommended verdict: `PASS_WITH_DECLARED_LIMITS` (offline/filesystem). Not production-ready; F04,
> F06, F07, F10, F15 and F16 are not closed overall.

> **PF-A3.1 audit addendum (2026-10-07, uncommitted over `4d7fe76`).** The independent audit of `26b6ed4..4d7fe76`
> (`_claude_outputs/ops/PF-A3.1/audit-findings.json`: 9 rows, 7 distinct findings, all confirmed) is closed in the OPS
> paths with 11 new regressions in `test_artifacts.py`; every one fails on `4d7fe76` and passes now. (AF-1/AF-9)
> `restore-instance` takes the runtime `.env` only from the verified `config_env` payload (format 1: the `.env` of the
> verified source payload) and every state file only from its verified `state/<name>` payload, read through one
> no-follow descriptor and re-hashed as read; a listed state file without that payload is refused by the legacy
> migration (`manifest-schema-unsupported`) and the schema-1 reader (`recovery-state-file-refused`) — LG-11 (unlisted
> `configuration/.env`, unlisted `configuration` link), LG-12, PB-11. (AF-2/AF-7) `release-check` prints
> `describe_deployed_source()` and exits 0 after an unprovable rollback (DA-14 release-check case). (AF-3) a pre-A3.1
> pointer written by `rollback:`/`restore:` counts as a commit only when the protected source manifest records it as
> `git_commit` (DA-17). (AF-4) a bundle folder over 20000 entries is `[invalid: bundle-unlisted-file]` instead of
> aborting the listing (LG-13). (AF-5) the pending-route copy and the controller's `resume` refusal name a healthy
> checkpoint, never the recorded preservation capture (EP-15; the CLI gate refuses `resume` before the controller).
> (AF-6) a drifted workspace over the archive limits is refused in the capture preflight before any confirmation or
> pause (`workspace-archive-limit`, EP-14), and `pf backup --emergency` preserves the data with a `workspace` exclusion
> (EP-13). (AF-8) GNU long-name/long-link and PAX headers declaring more than 64 KiB are refused before tarfile reads
> them (AX-18). Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (3.12.15)
> and `python:3.9` (3.9.25) containers, uid 0: **782 tests OK, 0 skipped** on both (858.581 s and 850.685 s; logs
> `evidence/suite-python3*-audit.log`). Updated without weakening: the two `restore_runtime_environment` unit tests now
> pass verified bytes, and the SS-3 allowlist gains the two verified-bytes write sites. Nothing was run against a NAS,
> DSM, Docker daemon, the running stack or a development database. Verdict unchanged: `PASS_WITH_DECLARED_LIMITS`.

> **PF-A3.2 checkpoint addendum (2026-10-07) — operation journal, resume/abandon, workspace generation switch.**
> Contracts first: AM-1..AM-10 in `contracts/lifecycle-records.schema.json` (byte-equal to `pf_config.LIFECYCLE_SCHEMA`);
> the four A3.1 plan/journal corpus files rewritten to the amended shape with unchanged expectations, every other A3.1
> corpus file byte-identical; 6 new valid examples, 9 invalid files, `example-app.env`; `cases.json` 54 → 70 rows.
> `pf_instance` gains the operation store (`scan_operations`, `write_plan_once`, `write_journal_generation`, private
> lists, `boot_id`, `process_start_ticks`, the descriptor-relative no-replace rename and the generation container);
> `pf_config` the pure index, route table, `legal_next`, resume decision, gate and workspace reconciliation;
> `pf_runner` `spawn_callback`; `pf_install` the journal-aware installer gate (§3.11a); `pf-admin.py` the plan/journal
> protocol for every lifecycle kind, `resume [--operation] [--abandon | --keep-workspace]` and the aliases, the
> still-running probe, the W1–W4 workspace switch with the §3.7a interval exception, frozen app/admin configuration,
> phase-aware `fail_closed`, staging cleanup and the operations block of `status`/`doctor`/`instances`.
> `pf_bootstrap.py`, `pf.sh`, `pf_docker.py`, `pf_source.py`, `compose.nas.yaml` and the shell wrappers are
> unchanged.
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (CPython 3.12.15) and
> `python:3.9` (CPython 3.9.25) containers, uid 0, source mounted read-only and copied to `/tmp/r`: **918 tests OK, 0
> skipped** on both (1476.924 s and 1465.037 s). Baseline: the 782 PF-A3.1 tests pass, updated without weakening per
> SPEC §6.2 (journal assertions instead of `pending.json`, the replace-source tests as W1–W4 tests, DT-3 over
> `OperationView` fixtures, the SS-3 allowlist reclassified with reasons, EH-* on a real operation). New: 136 tests in
> `test_operations.py` (SchemaAmendments 11, Store 13, StoreInstance 4, Routes 10, CrashMatrix 6 — 198 seam rows over
> deploy, update with migration, rollback `--restore-db`, reset-db, backup and restore-instance —, Resume 17,
> ResumeDatabase 3, RestoreResume 7, Protocol 8, RunnerInterrupt 1, Workspace 20, ConfigConcurrency 6, FailClosed 6,
> PurgeSurvival 6, Installer 3, ProcessProbes 8, CliRestart 5, CliPurgeSignals 2; several methods cover more than one
> case ID). After the gate runs only the module docstring of `test_operations.py` changed; that module was re-run on
> both images (136 OK, 418.758 s and 423.009 s) with `PF_A32_EVIDENCE` set. A final full-suite re-run on the final tree exited 0 on both images (stdout and exit code only; counts are those above). Evidence: `CRASH_MATRIX.json`,
> `ROUTE-TABLE-1`, `OPEN-HANDLE-1`, `RESUME-CLI-1-CL-*` (installed launcher, real SIGKILL/SIGTERM), `RO-13`; static
> checks in both images (`ast` (3, 9) on 20 files, `sh -n` on 4 scripts, the strict JSON reader on 102 contract files);
> secret scan of the logs and evidence (the only hit of the fixture password string is the fixture database name
> `pf_keep_20261001t000000z_abc123`). No NAS, DSM, Docker daemon, `/usr/local/bin/pf`, running stack or development
> database was contacted.
> *Completion run (2026-10-08).* The missing installed-launcher rows are implemented in `CliLifecycle`: real `pf
> backup`, `pf update`, `pf rollback --restore-db`, `pf purge` and `pf restore-instance` runs through the installed
> launcher on an optional simulated application plane of `tests/fake_docker.py` (per-service containers, images with an
> Alembic contract, a JSON database model behind `psql`/`pg_dump`/`pg_restore`/`createdb`/`dropdb`, the upgrade
> setting a database's heads; `block` gains `env`/`argv_match` selectors and `apply: after|before`). The disposable
> fixture root installs the repository release with one test-only change, its `pf-admin.py` entry block
> (`protected_fixture.fixture_release_files`): GitHub API answers from a fixture file, a local `file://` approved
> remote and, for CL-11, a patched `TIMEOUT_DATA`; every production check runs unchanged. New: CL-1 (backup SIGKILLed
> in `pg_dump` → new attempt), CL-3 (`apply: after`: resume forwards with exactly one live `alembic upgrade`), CL-4
> with RS-25b (`apply: before`: `needs_operator` with no Alembic call; a running owned one-off refuses the superseding
> `rollback --restore-db` before its plan; then it supersedes and completes), CL-11 (real runner timeouts: backup
> `pg_dump` and verification `pg_restore` → `failed_preserved`, the runner record reconciled; the live upgrade past the
> timeout → resume forwards or writes `needs_operator`), CL-12 (`.env` listener/password and `pf-config.json` health
> timeout/`auto_update` edited after the freeze: every Compose child of the resume carries the frozen env-file and URL
> hash, the frozen 180 s health timeout waits out a `starting` frontend, both proposal notes), CL-13 (rollback
> SIGKILLed in `restoring-candidate`, `.env` edited), CL-14 (restore-instance after a purge with an edited `.env`:
> `.env.proposal-<op8>`, SIGKILL after the `.env` write, resume binds the bundle snapshot), RS-18 (purge SIGKILLed
> inside the `ALLOW_CONNECTIONS` window → resume closes the flag and reopens) and RS-26b (a session on the maintenance
> database refuses a `database-switch` resume). One defect found and fixed: a resumed process restarted the
> `compose-<n>.json` render numbering and failed `File exists` on its first Compose envelope (`bind_operation` now
> continues the sequence). `CHECKPOINT = "PF-A3.2"`. Executed on the final tree, same command, uid 0: **927 tests OK,
> 0 skipped** on `python:3.12` (CPython 3.12.15, 1355.554 s) and `python:3.9` (CPython
> 3.9.25, 1386.263 s): the 782 baseline tests plus 145 in `test_operations.py`. `CRASH_MATRIX.json`
> now has 14 installed-launcher rows (SIGKILL, SIGTERM, timeout). Static checks re-run in both images; pyflakes
> leaves only the two pre-existing warnings of the untouched `tests/test_install.py`.
> Gates: A3-T04 **blocked** (real Docker, PF-A3.4) — offline part passed (crash matrix, RS-*, RO-13) and installed-CLI
> part **passed** with the fake daemon (CL-1..CL-5, CL-8..CL-11, RS-18, RS-25b; CL-6/CL-7 reachability evidence for the
> workspace interval); A3-T05 **blocked** (PF-A3.4) — offline part passed (RS-1..RS-4), CLI part passed (CL-3, CL-4,
> CL-11 migration branch); A3-T09 **passed** at `filesystem_and_cli` (CF-*, RS-27, RS-35, RS-36, CL-12..CL-14); A3-T10
> **blocked** at its DSM/btrfs/SMB level (PF-A5.1) — filesystem part passed (WS-1..WS-20, `OPEN-HANDLE-1`; WS-21..WS-23 after the audit). Earlier
> A1/A2/A3.1 offline cases re-run **passed**; A2-T04, the A2-T09 SMB part, the A1-T17 DSM part, A1-T11..T14, the
> A3-T03 real DB part, the launcher and reboot/power loss stay `not_run`/`blocked`. Declared limits: SPEC §10 and
> SYNOLOGY_ADMIN §18 (PF-A3.2 limits). The SPEC §8 definition of done holds offline; proposed verdict for the PF-A3.2
> audit: `PASS_WITH_DECLARED_LIMITS`. Not production-ready; no finding is closed overall.

> *Audit fix run (2026-10-08).* The PF-A3.2 audit (`_claude_outputs/ops/PF-A3.2/audit-findings.json`, nine confirmed
> findings) is fixed: the pending switch waits while its paired backup is open (F1, RO-13 previously checked
> single-operation journals only); `resume --abandon` in the pre-data rows reopens/withdraws as `resume` does there,
> dropping owned candidates and restoring a purge's connection flags (F2); refusing observations, the keep-workspace
> rows and the input re-read are decided before the confirmation, and a re-entry no longer overwrites the original
> `inventory-preflight.json` (F3); supersession ignores a stepped-back clock (F4); journal-less runner-record sources
> are documented (F5); a failed seal is never redone, the operation closes `failed_preserved` (F6); a superseded
> operation's staging is swept once its chain closed (F7); runner records follow a supersession chain (F8);
> `--keep-workspace` over a foreign directory records the retained generation (F9). 15 new tests (OS-8c, OS-15,
> RO-14, RS-38..RS-42, RS-18b, SG-5, WS-17b, WS-21, WS-21b, WS-22, WS-23) plus stronger OS-14 and WS-12/13; every new
> or changed test except WS-21b failed on `38a40cf` and passes now. Executed, same command, uid 0: **942 tests OK, 1
> skipped** on `python:3.12` (1616.440 s) and `python:3.9` (1599.460 s); the skip is RO-14, which needs
> `docs/deployment` (not copied by the canonical command) and passed on both images with the docs copied
> (`evidence/audit-*.log`). `CRASH_MATRIX.json` and the CLI evidence were not regenerated (no row changed). No NAS,
> DSM, Docker daemon, running stack or development database was contacted.

> **PF-A3.3 checkpoint addendum (2026-10-08) — integrated operations, exact-bundle functional verification,
> side-by-side recovery, capacity, cleanup, acknowledgement.** Contracts first: AM-11..AM-18 in
> `contracts/lifecycle-records.schema.json` (byte-equal to `pf_config.LIFECYCLE_SCHEMA`, `schema_version` stays 1, all
> additive), 9 new valid examples and 10 new invalid files, `cases.json` 70 → 89 rows. `pf_config` gains the pure
> reconcile-report parser and application-invariant oracle, the capacity model, the cleanup candidates and the
> acknowledged runner-record state; `pf_docker` the isolated-topology model, its validator and the isolation findings;
> `pf_source` the image-archive proof; `pf_instance` the write-once file helper, the tree identities and the
> registry state transaction `write_record_state`; `pf-admin.py` the isolated topology and the exact-bundle functional
> verification (instance purge gate, `restore-instance` identity/database-image rules, `restore-instance --side-by-side`
> as the journaled `restore-side-by-side` operation), the current-data protection of `reset-db`, `rollback` and
> `abort-deploy`, the capacity preflight, `pf cleanup`, generation sealing and `resume --acknowledge`. `pf.sh`,
> `compose.nas.yaml`, `pf_bootstrap.py`, `pf_runner.py`, `pf_install.py` and the shell wrappers are unchanged.
> *Registry tombstone (owner decision OD-A33-08, applied).* The instance purge writes `state: purged` through a
> registry transaction (the registry lock is taken non-blocking while the instance lock is held; `check_authority`
> admits only the instance purge's own state write), releases the Compose project claim, and `restore-instance` and
> `deploy` re-check the claim under the registry lock before they write `registered` again; `abort-deploy` of such a
> deployment and `resume --abandon` of such a restore return the record to `purged`. Tests TB-1..TB-4 cover the restore
> into a purged record, a second instance claiming the released project (`instance-claim-taken`), and a crash before and
> after the registry write (`test_tb3`, resume ends in exactly one tombstone). OD-A33-06/07/09 keep the spec defaults
> (recorded deviations awaiting owner approval): a recovery target has no listener and is not a registered instance, and
> another instance's bundle is refused (`restore-target-mismatch`).
> Executed: `python -B -m unittest discover -s tests -p 'test*.py'` in disposable `python:3.12` (CPython 3.12.15) and
> `python:3.9` (CPython 3.9.25) containers, uid 0, source mounted read-only and copied to `/tmp/r` (the canonical
> command): **1016 tests OK, 1 skipped** on both (2059.229 s and 2058.761 s;
> `_claude_outputs/ops/PF-A3.3/evidence/gates-2-python3.*.log`). Baseline: the 942 PF-A3.2 tests all pass, updated
> without weakening where an A3.3 rule replaced an A3.2 sentence (the purge gate now requires the passed
> `functional_recovery_verified` record of the operation; the registry record is `purged` after the instance purge;
> `restore-instance` mismatches are decided by identity; the former `pf_recovery_*` mode is gone; the SS-3 allowlist of
> write sites gained the A3.3 writers; `PENDING_ROUTE_COMMANDS` and the `DISPATCH` count follow the new routes;
> `pf.CHECKPOINT` is `PF-A3.3`). New: **74 tests** — 73 in the new `tests/test_integrated.py` (IsolatedModel, ReconcileReport,
> ImageArchive, CapacityPure, SchemaA33, PurgeIntegrated with TB-1..TB-4, IsolatedTopology, RestoreTarget, SideBySide,
> Cleanup, Acknowledge, ResetRollback, AbortDeploy, CapacityIntegrated, InstalledCli; 73 OK on both images in 124.133 s
> and 125.082 s, `evidence/integrated-v-python3.*.log`) and PZ-13 in `test_artifacts.py` (an A3.2-opened purge plan resumes
> in `deleting` under A3.3 with its `data_restore_verified` record). `fake_docker.py` gained the reconcile one-off and
> `protected_fixture.py` the isolated-stack double and `DockerRootDir`; no behaviour changes without the new state keys. The one skip is RO-14 (it reads `docs/deployment`, which the canonical command does not copy); with
> `docs/deployment` copied it passes on both images, after the documentation edits of this checkpoint
> (`evidence/ro14-docs-python3.*.log`). Static checks: all 21 `.py` files under `deploy/synology` parse with
> `ast.parse(..., feature_version=(3, 9))`; every contract JSON file loads; pyflakes reports nothing in the changed
> files (two warnings remain in the untouched `tests/test_install.py`, `evidence/static-checks.log`).
> Acceptance statuses are in `_claude_outputs/ops/PF-A3.3/ACCEPTANCE_RESULTS.json`. Offline, with the fake daemon:
> A3-T15 (isolation of the `pfverify-*`/`pfrecover-*` topologies from the daemon's reported configuration, incl. the
> loopback-port case) is `passed` for its offline part; A3-T06, A3-T07, A3-T08, A3-T14 (filesystem part, real
> `statvfs`) and A3-T16 are **`partial`** because some planned case IDs have no dedicated test (listed per case in the
> JSON: for example XC-1/XC-3/XC-7 through the launcher, PZ-5, RX-3/RX-4/RX-6/RX-7/RX-9, SB-5..SB-9, CU-4/CU-5/CU-8/CU-13,
> RP-3/RP-5/RP-8/RP-9, CP-2/CP-4..CP-6, FV-3/FV-7/FV-8); where an earlier test already exercises the behaviour it is named
> as such, not as a new pass. All six are overall **`blocked`** at their required real Docker/PostgreSQL level
> (PF-A3.4). Evidence JSON written by the suite is in `evidence/offline/` (`PURGE-GATE-1`, `FUNCTIONAL-1`,
> `ISOLATION-1`, `RESTORE-TARGET-1`, `CAPACITY-1`, `CLEANUP-1`, `ACK-1`, `XC-2`, `XC-4`, `XC-5`, `XC-6`); no
> `CRASH_MATRIX.json` was generated for the new effects. Declared limits: nothing was run against a NAS, DSM, SMB, a
> real Docker daemon, real PostgreSQL, the live Compose stack or a development database; isolation is proven from the
> daemon's reported configuration only; the application invariant oracle is `app.cli reconcile` of the deployed image
> and its output is never stored; the Docker root is shared by every instance (no reservation); the reconcile child
> shares the runner's ten-minute limit. Proposed verdict for the PF-A3.3 audit: `PASS_WITH_DECLARED_LIMITS` only if the
> audit accepts the `partial` offline parts; not production-ready, no finding is closed overall.

> **PF-A3.3 audit addendum (2026-10-08) — fixes for `_claude_outputs/ops/PF-A3.3/audit-findings.json`.** The audit of
> `d2d99bf..3f0da91` found that the side-by-side `image-load` effect ran the exact restore's action and rewrote the live
> `active-images.yaml`; it is now load-only (re-proof and retag check right before `docker image load`, then the
> topology's image IDs; no override write, no tag verification). Further fixes: a valid earlier generation seal is never
> deleted (`generation-seal-exists` keeps both when the tree changed); an abandon interrupted after its `purged`
> write-back is completed by `resume --abandon` (`abandon-in-progress` for a forward resume); the acknowledgement counts
> sessions only on the databases its records' database effects name; the instance purge's recovery need includes the
> image archive (`docker image inspect` Size); a displaced checkpoint history with a non-checkpoint entry or an active
> copy that does not read strictly is unique; the throwaway password files of a topology without Docker resources are
> removed (pre-deletion failure, orphan files after a crash before `topology.json`, stale `isolation-preflight-*` /
> `verify-source-*` at close). OD-A33-06/07/09 are documented as declared deviations awaiting owner approval. New
> tests in `test_integrated.py` (73 → 85): SB-6, TB-5, CU-5/CU-6 re-seal (2), CU-12 variants, CU-13 subset, FV-3, PZ-5,
> RP-8, CP-2 purge row, AK-7, IT crash window; IT-3 and CU-7 extended (CU-7 now uses a real `pf backup` checkpoint).
> Fixture changes, no assertion weakened: `fake_docker` reports image `Size` and answers the database-scoped
> acknowledgement query; the `test_pf_admin` FakeController reports `Size`; the SS-3c allowlist names
> `discard_topology_files` instead of `isolated_topology` for the topology-file unlink. Each code fix's regression test
> fails with the `3f0da91` product modules and passes after the fix
> (`_claude_outputs/ops/PF-A3.3/evidence/audit-before-fix-python3.12.log`: 9 of the 14 named tests fail with the
> HEAD modules, the 5 coverage-only tests pass; `audit-after-fix-python3.12.log`: 14 OK). Executed with the canonical
> command on the final tree: **1028 tests OK, 1 skipped (RO-14)** on `python:3.12` (1973.718 s) and `python:3.9`
> (1972.783 s), `evidence/audit-suite-python3.*.log`; RO-14 with `docs/deployment` copied passes on both
> (`evidence/audit-ro14-docs-python3.*.log`); `ast.parse(..., feature_version=(3, 9))` and pyflakes are clean on the
> changed files (`evidence/audit-pyflakes.log`).
> `CRASH_MATRIX.json` (210 in-process rows for the new effects) found NF-1, not fixed here: a `reset-db` of a
> schema/image-mismatched instance interrupted in `preserving` can be neither resumed nor abandoned (both reopen and
> refuse on the head mismatch); see `IMPLEMENTATION_REPORT.md` §8. Acceptance stays conditional on the owner approving
> OD-A33-06, OD-A33-07 and OD-A33-09.

> **PF-A3.3 completion addendum (2026-10-08) — NF-1 and the remaining SPEC §6.1 risky-path tests.** Uncommitted on
> top of `1b44831` (OPS paths only). *NF-1 fixed:* a `reset-db` planned on a schema/image mismatch now carries the
> precondition `contract:schema-image-mismatch` on its `checkpoint:before-reset` capture; before any live database
> effect `pf resume` goes forward (the emergency preservation is re-observed or re-run, then the reset completes) and
> `pf resume --abandon` drops the owned clean candidate and closes `cancelled` without a reopen (services as the reset
> left them; the message names `pf reset-db` and `pf rollback <checkpoint> --restore-db`); a consistent instance keeps
> the A3.2 reopen. The `preservation-failed` and `reset-images-unidentified` copies name the abandon for such a reset.
> *Defects found by the new tests and fixed:* `restore-instance` capacity now counts `images.tar` when a bundle image is
> absent (preflight and the `preparing-target` re-check); `status` reports `pf_recovery_*` databases with the
> `legacy-recovery-database` note; a preflight `capacity-insufficient` names its phases ("<phase> needs"). *New tests*
> in `tests/test_integrated.py` (85 → 113): NF-1 (2), SB-5, SB-7, SB-8, SB-9, FV-7, FV-8, PZ-6, RX-4, RX-6, RX-7, RX-9,
> RP-3, RP-5, RP-9, CP-2, CP-4 (backup; update in `CapacityUpdate`), CP-5, CP-6, CU-4, CU-5, CU-8, CU-13 (plan-drift,
> refused `image rm`), XC-1, XC-3, XC-7 (the installed launcher at a scripted pty terminal that types every asked
> phrase, the random `ERASE` challenge included); RP-8 extended. The regression tests fail with the `1b44831` product
> modules (`_claude_outputs/ops/PF-A3.3/evidence/completion-regression-before-fix-python3.12.log`). Executed with the
> canonical command: **1056 tests OK, 1 skipped (RO-14)** on `python:3.12` (2033.061 s) and `python:3.9` (2026.765 s),
> `evidence/completion-suite-python3.*.log` (a first run failed one test on `python:3.12` only, SB-7 selecting an
> operation by sort order within one second; fixed in the test, both images re-run). RO-14 with `docs/deployment`
> copied passes on both; `ast.parse(..., feature_version=(3, 9))` and pyflakes are clean on the changed files.
> `CRASH_MATRIX.json` regenerated (234 in-process rows; every `resume` row ends `completed` or `cancelled`; the NF-1
> rows now end `completed`/`cancelled`). Declared interpretations: a legacy bundle without a backend image is refused by
> the strict reader (`manifest-schema-unsupported`) before the side-by-side rule; the CU-13 "DaemonFailure on `image
> rm`" row is a daemon refusal of the command (an unreachable daemon stops the cleanup open, earlier test); a cleanup
> crashed before its first effect closes `cancelled`. Acceptance: every offline part of A3-T06/T07/T08/T14/T15/T16 is
> `passed`; all stay `blocked` at their real Docker/PostgreSQL levels (PF-A3.4). Not production-ready; no NAS, DSM,
> Docker daemon, running stack or development database was contacted.

> **PF-A3.4 addendum (2026-10-09) — real Docker/Compose/PostgreSQL integration and lifecycle-loop audit.** Uncommitted
> on top of `7b24d10` (OPS paths only; the PF-A3.3 completion delta above was committed as `181a806`, so its
> "uncommitted" wording is historical). *Environment:* a privileged `docker:28.5.1-dind` container on Docker Desktop for
> Windows (inner Engine 28.5.1, Compose v2.40.3, PostgreSQL 16.15, Alpine CPython 3.12, loop-mounted sparse ext4 for the
> Docker root and the instance tree, base images pinned by digest), driven by `deploy/synology/integration/`
> (`run-isolated.sh`, `dind-entry.sh`, `host_inventory.py`, `harness/`); the installed launcher `/usr/local/bin/pf` ran
> at a scripted pty as container root; the release source is the fixture seam (local `file://` remote). *Evidence runs*
> (one equality set): `20261008T230113Z-8f17ef74` (setup, L1, L2, L7, L3, cases, L6, ctl), `20261008T230114Z-b3f4216e`,
> `20261008T230115Z-89c777fa`, `20261008T230117Z-d050aee3` (setup, L1–L3, L7 and the L4 row groups); every host
> inventory diff is clean; package `_claude_outputs/ops/PF-A3.4/`. *Results:* LOOP-01 (A3-T11) and LOOP-02 (A3-T12)
> passed in all four runs; LOOP-03/A3-T03, LOOP-05, LOOP-06, LOOP-07/A3-T15, A1-T11, A1-T12, A1-T14, A2-T04, A3-T06,
> A3-T07, A3-T08 and C-CONC-01 passed; A1-T13 passed except its rootless part (blocked); the real crash matrix ran all
> 66 R rows, 23 boundary rows and C-A3-16 (90 records): 71 passed, R10 and R65 failed (F-A34-03), R09 blocked (an update
> never writes phase `finalizing`), R29, R31 and R33 blocked by product failures before their windows (F-A34-08,
> F-A34-07, F-A34-09), R43 and B01–B12 blocked because the R33 dead end left the instance purged, R66 blocked; the grid
> has no gap, and 13 phase-table entries lost their rows to that cascade. *Fixed product defects (regression first):*
> F-A34-01 the available-extension grammar refused `uuid-ossp` (every real `rollback --restore-db` failed;
> `test_artifacts.Postgres.test_pg10`); F-A34-02 `pg_dumpall -d postgres` is a libpq connection string (every real purge
> cancelled; `test_integrated.PurgeIntegrated.test_pz16`, fake calibrated); F-A34-09 `restore-instance` routed a
> retained `pf_restore_*`/`pf_migrate_*` store to the rollback/update branch (`plan-input-changed`, dead end with the
> instance purged; `test_integrated.RestoreTarget.test_rx10`, failed with the base module) — fixed after the evidence
> runs, its real re-run is pending. *Open (changes required):* F-A34-03 (no route starts a db container stopped from
> outside: R10, R65; OD-A33-12 stays open), F-A34-04/F-A34-06 (refusals not effect-free: C-A3-14), F-A34-05
> (observation, C-A1-14 (c)), F-A34-07 (first-start readiness race, R31), F-A34-08 (masked `createdb` failure, R29);
> A12r2-F06: R41 proven, R66 blocked (a read-only `pg_dump` never has a runner record). *Offline:* D6
> `tests/test_integration_harness.py` (28 tests) OK on `python:3.12` and `python:3.9`; `ast.parse(...,
> feature_version=(3, 9))` and pyflakes clean on every changed or new Python file; the RX-10 regression errors with the
> base module and passes after the fix. F-A34-10: W1's durability barrier was a global `os.sync()`, which never
> returned once an unrelated filesystem hung (after the evidence runs the Docker Desktop VM's internal FUSE mount
> `/run/jfs` did, and every update, rollback and suite run stopped at W1); it is now an fsync of every staged directory
> and file and of the generation container (`test_artifacts.Emergency.test_ep10b`). The canonical suite on the final
> bytes: 1088 tests OK, 1 skipped, on `python:3.12` and `python:3.9`. The real re-run of R33 and its loops with the
> fixed bytes is pending. **Gate: CHANGES_REQUIRED.** Not production-ready; no NAS, DSM, SMB, real Synology host,
> the running `partflow` stack or the development database was contacted.

> **PF-A3.4 fix-round addendum (2026-10-09).** Uncommitted on top of `182b265` (OPS paths only). The open product
> findings of the real runs are fixed with offline regressions that fail on the `182b265` bytes and pass after:
> F-A34-03 (R10, R65: every route that needs the database starts the instance's own stopped db container with
> `compose start db`, never a create, and the resumed activation does so before its `--no-deps` backend start;
> `test_integrated.StoppedDatabase.test_sd1`/`sd3`/`sd4`, `test_artifacts.Emergency.test_ep16`, guard `test_sd2`);
> F-A34-07 (R31: the db readiness wait also requires TCP `pg_isready` on 127.0.0.1, which the image's temporary
> initialization server never answers; `RestoreTarget.test_rx11`); F-A34-08 (R29: a failed verification `createdb`
> reports the server's answer instead of an assumed missing locale; `PurgeIntegrated.test_pz17`; root cause unproven);
> F-A34-04 observed part (C-A3-14 (a): backup and purge refuse on capacity before the contract probe;
> `CapacityIntegrated.test_cp8`; refusals after the probe still keep its tags); F-A34-06 (C-A3-14 (b): a command that
> wrote no runtime state leaves no empty `state/`; `RestoreTarget.test_rx12`); F-A34-05 (C-A1-14 (c): a link or special
> entry of a drifted workspace is refused in the preflight, `workspace-unsupported-entry`; EP-12 rewritten). Base-bytes
> run: `D:/.claude-tmp/part-flow/ops/PF-A3.4/logs-fix/regressions-base-py312.log` (11 failures over 10 tests; the guard
> passes). Correction to the PF-A3.4 addendum above: its "1088 tests OK … on `python:3.12` and `python:3.9`" is backed
> by a complete log for `python:3.9` only (`logs/PF-A3.4-gates-3-py39.log`); the `python:3.12` log of that run has no
> result line. *Canonical suite on the fix-round bytes:* 1097 tests OK, 1 skipped (RO-14), on `python:3.12` (2311.5 s) and `python:3.9` (2306.8 s), exit 0 (logs `D:/.claude-tmp/part-flow/ops/PF-A3.4/logs-fix/gates-4-py312.log`, `gates-4-py39.log`, copied to the package's `evidence/offline/`). `ast.parse(..., feature_version=(3, 9))` and
> pyflakes are clean on every changed Python file. The real re-run of R10, R29, R31, R33 and R65 and their loops is
> **pending** a Docker Desktop restart (an owner action: the VM's global `sync()` still hangs). **Gate:
> CHANGES_REQUIRED** (unchanged until that re-run). Not production-ready.

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
