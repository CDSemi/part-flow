# Lifecycle record examples (PF-A3.1, PF-A3.2, PF-A3.3)

These files are the executable acceptance corpus of `contracts/lifecycle-records.schema.json`
(A3-T01). They are not host state, they are not shipped in a control release, and nothing reads
them at runtime. Every value is synthetic: instance IDs, hashes, image IDs and the example
configuration are made up, and no example carries a real secret.

Every record example is stored as exactly `pf_instance.normalize_json(value)` (sorted keys, no
whitespace, no trailing newline), the one byte form a writer produces and a schema 1 reader accepts.
Legacy inputs (`legacy-format*.json`, other than the `-migrated` files) are stored in the v2.5
`write_json` form (indented, trailing newline), as v2.5 wrote them.

`cases.json` binds each file to its expected outcome:

```json
{"file": "<name>", "record": "<$defs record name | legacy>",
 "expect": {"valid": true | false, "problem": "<substring of one problem> | null"},
 "plan": "<operation_plan file the journal binds to (operation_journal rows only, PF-A3.2)>"}
```

PF-A3.2 (SPEC section 2.3, amendments AM-1..AM-10, `schema_version` still 1): an `operation_journal` row names
the plan it binds to, and the journal is also checked against that plan (`pf_config.lifecycle_problems(journal,
"operation_journal", plan=plan)`: effect IDs, `workspace_sync_pending` only for a switch or pending plan, the purge
and abort-deploy deletion approval, the restore-instance abandon). AM-10 fixes the meaning of
`operation_plan.frozen_config`: `sha256` is the SHA-256 of the **rendered `app.env` bytes** of the snapshot the
operation consumes (`env_file_sha256` of its `frozen-config.json` record) and `bytes` their length; never the hash of
the snapshot record (which embeds `created_at` and `operation_id`) and never the hash of the editable `.env`.
`example-app.env` holds the synthetic rendered bytes every PF-A3.2 example plan references (no real secret).

PF-A3.3 (SPEC section 2.4, amendments AM-11..AM-18, `schema_version` still 1): two operation kinds
(`restore-side-by-side`, `cleanup`) with their phases and per-kind effect types (AM-15), the `preserving` phase of
`abort-deploy` (AM-18: its capture follows a writer stop and names the bundle in `preservation_refs`), the
`functional_recovery_verified` record rules for a purge bundle (AM-13: every functional check present, isolation and
health checks run, `not_run` only with `unavailable:`/`excluded:`), and two new records: `runner_acknowledgement`
(the attended acknowledgement of runner records in a directory without a journal) and `generation_seal` (a retired
workspace generation). New retained-artifact kinds: `isolated-topology`, `recovery-target`, `generation-seal`.

Legacy rows also carry `bundle_kind`, `payload_sizes` (the verified payload sizes the reader
passes to the migration) and `migrated` (the expected migrated manifest). The test reads the input
bytes, migrates them with `pf_config.migrate_legacy_manifest` (legacy hash = SHA-256 of the input
file bytes) and requires the result to equal the `migrated` file byte for byte; the migrated file is
then checked as a `recovery_manifest` row of its own.

| File | Expected outcome |
|---|---|
| `checkpoint-healthy.json` | Valid healthy checkpoint bound to a deployment record (`pf backup`, writers running). |
| `checkpoint-emergency.json` | Valid emergency preservation before a rollback: schema-image mismatch (live `r2`, image `r1`). |
| `checkpoint-emergency-deployment-image.json` | Valid emergency preservation with a `deployment-image-mismatch`. |
| `checkpoint-partial.json` | Valid partial capture: no backend image identity, workspace source of unknown provenance. |
| `purge-bundle.json` | Valid purge bundle: two stores in one writers-stopped group, row counts, db image archived. |
| `deployment-record.json` | Valid DeploymentRecord of an update. |
| `verification-record.json` | Valid `data_restore_verified` record (rows `not_run`: writers were running). |
| `verification-functional-schema-valid.json` | Valid by schema: a checkpoint's functional record (the AM-13 functional rules bind purge bundles only). |
| `verification-record-functional.json` | Valid PF-A3.3 `functional_recovery_verified` record of a purge bundle: every functional check, one `topology:` check, the topology removed. |
| `verification-record-functional-unavailable-app-check.json` | Valid: `app-invariants` `not_run` with `unavailable:` (an image without the reconcile command). |
| `verification-record-functional-legacy.json` | Valid: a legacy bundle (`config:bundle` and `deployment:record` `not_run` with `unavailable:`/`excluded:`). |
| `operation-plan-restore-side-by-side.json` | Valid side-by-side recovery target: input bundle, topology effects, workspace untouched. |
| `operation-plan-cleanup.json`, `operation-journal-cleanup-deleting.json` | Valid cleanup (no input bundle, cleanup effect types only) interrupted in `deleting`. |
| `operation-plan-abort-deploy-preserving.json` | Valid abort-deploy after the frontend opened: writer stop and `checkpoint:before-abort` before the deletion. |
| `runner-acknowledgement.json`, `generation-seal.json` | Valid PF-A3.3 records. |
| `operation-plan.json`, `operation-journal.json` | Valid OperationPlan/OperationJournal of an update without a schema change (rewritten to the PF-A3.2 amended shape: effect phases, `supersedes`, `admin_config`, `workspace`, `input_bundle`, `deletion`; exact command lines in `recovery_route`/`legal_next`). |
| `operation-plan-update.json`, `operation-journal-update-migrating.json` | Valid update with a rehearsal; the live migration is `unknown` after an interrupt (pre-heads recorded as evidence). |
| `operation-plan-purge.json`, `operation-journal-purge-deleting.json` | Valid purge: no deletion hash in the plan; the binding deletion plan and backup/admin-config choices in `journal.deletion`. |
| `operation-plan-abort-deploy.json` | Valid abort-deploy superseding an incomplete deploy; its frozen deletion plan hash in the plan. |
| `operation-plan-restore-instance.json`, `operation-plan-update-keep.json` | Valid restore-instance (input bundle, workspace switch) and an update kept with `--keep-workspace`; the plans of two invalid journal rows. |
| `legacy-format2-checkpoint.json` -> `legacy-format2-migrated.json` | Healthy legacy checkpoint; provenance unknown, claims kept in `legacy`. |
| `legacy-format2-before-rollback.json` -> `legacy-format2-before-rollback-migrated.json` | Emergency preservation (`reason: legacy`, `claimed_reason: before-rollback`). |
| `legacy-format1-checkpoint.json` -> `legacy-format1-checkpoint-migrated.json` | Partial (no migration fingerprint); never a rollback target. |
| `legacy-format2-purge.json` -> `legacy-format2-purge-migrated.json` | Healthy legacy purge bundle; a non-connectable retained store adds the `retained-heads` limitation. |
| `legacy-format1-purge.json` -> `legacy-format1-purge-migrated.json` | Healthy legacy purge bundle with the `config_env` exclusion (the runtime `.env` is inside the source archive). |
| `invalid-*.json` | Each is a valid example with exactly one defect; `expect.problem` names the refusal. Three are raw bytes: `invalid-duplicate-key.json`, `invalid-nan.json` (the strict parser refuses them) and `invalid-not-normalized.json` (a schema 1 reader refuses any byte form other than the normalized one). |

A schema pass proves no runtime behaviour: payload hashes, archive members, verification records
and the PostgreSQL facts are checked by the strict reader, the importer and the captures, whose
tests are in `tests/test_artifacts.py`.
