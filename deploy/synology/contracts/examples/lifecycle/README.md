# Lifecycle record examples (PF-A3.1)

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
 "expect": {"valid": true | false, "problem": "<substring of one problem> | null"}}
```

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
| `verification-functional-schema-valid.json` | Valid by schema; no PF-A3.1 writer produces `functional_recovery_verified` (SC-6). |
| `operation-plan.json`, `operation-journal.json` | Valid frozen v1 OperationPlan/OperationJournal (no runtime writer in PF-A3.1). |
| `legacy-format2-checkpoint.json` -> `legacy-format2-migrated.json` | Healthy legacy checkpoint; provenance unknown, claims kept in `legacy`. |
| `legacy-format2-before-rollback.json` -> `legacy-format2-before-rollback-migrated.json` | Emergency preservation (`reason: legacy`, `claimed_reason: before-rollback`). |
| `legacy-format1-checkpoint.json` -> `legacy-format1-checkpoint-migrated.json` | Partial (no migration fingerprint); never a rollback target. |
| `legacy-format2-purge.json` -> `legacy-format2-purge-migrated.json` | Healthy legacy purge bundle; a non-connectable retained store adds the `retained-heads` limitation. |
| `legacy-format1-purge.json` -> `legacy-format1-purge-migrated.json` | Healthy legacy purge bundle with the `config_env` exclusion (the runtime `.env` is inside the source archive). |
| `invalid-*.json` | Each is a valid example with exactly one defect; `expect.problem` names the refusal. Three are raw bytes: `invalid-duplicate-key.json`, `invalid-nan.json` (the strict parser refuses them) and `invalid-not-normalized.json` (a schema 1 reader refuses any byte form other than the normalized one). |

A schema pass proves no runtime behaviour: payload hashes, archive members, verification records
and the PostgreSQL facts are checked by the strict reader, the importer and the captures, whose
tests are in `tests/test_artifacts.py`.
