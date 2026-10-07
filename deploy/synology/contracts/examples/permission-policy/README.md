# Permission policy examples (PF-A2.3)

These files are examples of the semantic permission policy document
(`contracts/permission-policy.schema.json`, the design r2 schema). They are not host
configuration, they are not shipped in a control release, and nothing reads them at
runtime. The approved policy of an instance lives only in its protected record
`<private_state>/permission-policy.json`, written by a confirmed `pf permissions apply`.

`shared.json` and `restricted.json` are byte copies of the design r2 examples.
`cases.json` binds each file to its expected outcome, so the tests (PS-5, CP-10) and the
documentation share one source:

| File | Expected outcome |
|---|---|
| `shared.json` | Valid. Compiled targets are listed in `cases.json`. |
| `restricted.json` | Valid. Compiled targets are listed in `cases.json`. |
| `invalid-unknown-key.json` | `permission-policy-invalid`: a numeric `"mode"` override is an unknown key. |
| `invalid-group-exec-none.json` | `permission-policy-invalid`: group execution needs group access. |
| `invalid-control-write.json` | `permission-policy-invalid`: control never gets group write. |
| `invalid-backup-write.json` | `permission-policy-invalid`: backups never get group write. |
| `invalid-control-group-exec.json` | `permission-policy-invalid`: control executables are owner only. |
| `invalid-config-executables.json` | `permission-policy-invalid`: configuration has no executable option. |
| `invalid-private-group.json` | `permission-policy-invalid`: private state has no group. |
| `invalid-version.json` | `permission-policy-invalid`: `policy_version` must be 1. |
| `invalid-duplicate-key.json` | `permission-policy-invalid`: the strict parser refuses a duplicated key. |
| `invalid-group-name.json` | `permission-policy-invalid`: a group with surrounding whitespace or `/`. |
| `missing-group.json` | Valid by schema. `permission-group-missing` on a host without the group `nas-editors`. |
| `unsupported-workspace-no-exec.json` | Valid by schema and compiles. `permission-policy-unsupported` when it would be activated (OD-A23-18). |

A schema pass proves no operating-system behaviour: groups, ownership, modes, ACLs, SMB
share permissions and the files clients create are checked on the host by
`pf permissions check`, and real DSM/SMB account tests belong to PF-A5.1
(PERMISSIONS section 7).
