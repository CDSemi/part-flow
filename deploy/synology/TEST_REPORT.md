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
