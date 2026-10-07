# Admin configuration examples (PF-A2.2)

These files are examples of `<configuration>/pf-config.json` documents and of one
path proposal. They are not host configuration, they are not shipped in a control
release, and nothing reads them at runtime.

`cases.json` binds each file to its expected outcome, so the tests (EX-1) and the
documentation share one source:

| File | Expected outcome |
|---|---|
| `new-install.json` | Schema 2, valid. What the pre-registration wizard writes for project `partflow-staging` with both groups `users`. |
| `existing-v1.json` | Legacy schema 1 (no `schema_version`), valid. `auto_update`, `ci_workflow`, `health_timeout_seconds` and `backup_read_group` are implicit. |
| `existing-v1.migrated.json` | The exact bytes `pf config admin` writes when it migrates `existing-v1.json`. The implicit values are the frozen schema 1 defaults, not the example's. |
| `missing-group.json` | Schema 2, valid by schema. On a host without the group `nas-editors` the wizard asks for an existing group. |
| `unsupported-old-schema.json` | `admin-config-version-unsupported`. Only a file without `schema_version` is legacy schema 1; an explicit `"schema_version": 1` is refused. |
| `unsupported-future-schema.json` | `admin-config-version-unsupported` (`schema_version: 3`). |
| `conflicting-path.json` | A case document, not a configuration. Pre-registration `pf config admin --configuration … --project …` refuses the proposal with `config-path-conflict` and creates nothing. |

`{base}` in `conflicting-path.json` is a placeholder that the tests replace with a
temporary directory.
