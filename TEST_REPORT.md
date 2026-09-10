# PartFlow NAS Admin v2 — Validation report

Date: 2026-09-08. Tool version: 2.0.0.

## Executed checks

| Check | Actual result |
| --- | --- |
| Python compilation of `pf-admin.py` | Passed with Python 3.13.5 |
| `sh -n` on `pf.sh`, `backup.sh`, `release-check.sh` | Passed |
| `pf.sh --help` through the real shell entry point | Passed |
| Offline unittest suite | **65 tests passed** |
| Filesystem/archive operations used by the tests | Executed against real temporary files |
| Local Git clone + checkout of a non-tip SHA | Executed successfully against a local test repository |
| Included Compose and environment examples vs original staging bundle | Byte-for-byte unchanged |

Reproduce the offline suite from the extracted package:

```sh
python3 -m unittest discover -s pf-admin-tests -v
python3 -m py_compile pf-admin.py
sh -n pf.sh
sh -n backup.sh
sh -n release-check.sh
```

The tests operate on temporary directories and simulated external services. They do
not connect to the NAS or run the application's own database integration suite.

## Coverage exercised

- Fixed-SHA checkout rather than following a branch after selection.
- Preservation of local scripts/configuration during source replacement.
- Verified checkpoint creation before update/reset/rollback.
- Dump failure and restore-verification failure stopping the operation.
- Manual migration approval, rehearsal before the live migration, and failure journaling.
- No automatic database downgrade or restoration on application startup failure.
- Code rollback preserving newer rows in the simulated database.
- Schema-incompatible code rollback rejection.
- Explicit database rollback retaining the displaced database.
- Clean-database reset and preservation of original simulated data.
- Refusal to terminate unrelated database sessions during a database switch.
- Exact confirmation text and refusal of noninteractive destructive operations.
- Newest-first checkpoint selection, 10-item pages and explicit backup selection.
- Checksum corruption and missing retained image rejection.
- Archive traversal/link rejection and source archive round-trip.
- File locks and persistent interruption guards.
- Check-only/default-disabled automation and required opt-in.
- Automatic migration and divergent-history rejection.
- Exact-commit CI matching and refusal to reuse an earlier successful CI attempt.
- Release tag resolution and detection of previously observed tags being moved.
- Stable versus pre-release filtering.
- Production lifecycle-operation guards.
- Managed one-off job labelling.
- Removing accidental shell database overrides while preserving deliberate rehearsal overrides.
- Blocking destructive Compose volume flags.

## Not executed / not certified

**Docker and PostgreSQL interactions were simulated.** No Docker daemon, actual
PostgreSQL 16 service, Synology DSM, Container Manager, NAS disk/ACL environment,
reverse proxy or real application workload was available for runtime validation.
The SQL transaction used for database rename/cutover was generated and checked in
tests, not executed against a PostgreSQL server here. PostgreSQL 16 documentation
and upstream rename implementation were inspected, but that is not a live SQL test.

A live shell Git clone of GitHub was attempted but the code sandbox could not
resolve `github.com`. The GitHub connector did read the project at commit
`8d358eea0582b2e910df60569ad9865fd78f9d98`, its deployment policy, CI workflow and
release metadata. HTTP success and clone performance from the user's NAS remain
unverified. The local Git test does not substitute for NAS network validation.

No PartFlow container image was built or started here. No real migration, data reset,
restore, reconciliation, application smoke test, or release deployment was performed.
No GitHub commit/push/release or DSM scheduled task was created.

Passing the offline suite is not approval for production. Rehearse the commands with
disposable staging data on the actual NAS before enabling scheduled application.
