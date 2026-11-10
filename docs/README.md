# PartFlow Documentation

> **Language:** English is the source of truth. Each active document has a
> Vietnamese mirror beside it with the `.vi.md` suffix — see
> [`README.vi.md`](./README.vi.md) for the Vietnamese index.

This index lists every active PartFlow document and what it is for. Start with the
[root README](../README.md) for an introduction and the developer guide. Each rule
has one owning document: `PROJECT_PROFILE.md` owns the domain rules,
`GUI_DESIGN.md` owns the approved user interface, `IMPLEMENTATION_ROADMAP.md` owns
the implementation order and the current implementation state, slice documents
hold subordinate detail, and `DEPLOYMENT.md` owns deployment. Every document
states only its current content and ends with a change history table.

## Canonical specifications

| Document | Responsibility | Vietnamese |
| --- | --- | --- |
| [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md) | The authoritative specification: domain terminology, business behavior, invariants, workflows, architecture and product scope. | [`PROJECT_PROFILE.vi.md`](./PROJECT_PROFILE.vi.md) |
| [`GUI_DESIGN.md`](./GUI_DESIGN.md) | The approved target user interface and interaction behavior, with [`mockups/partflow-gui-mockup-v18.html`](./mockups/partflow-gui-mockup-v18.html) as the visual reference. | [`GUI_DESIGN.vi.md`](./GUI_DESIGN.vi.md) |
| [`IMPLEMENTATION_ROADMAP.md`](./IMPLEMENTATION_ROADMAP.md) | Implementation order, phase boundaries, dependencies, the current implementation state and the temporary limitations. | [`IMPLEMENTATION_ROADMAP.vi.md`](./IMPLEMENTATION_ROADMAP.vi.md) |
| [`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md) | The data model and command contracts of Work Order intake and production release, and the persistence rules that later slices share; subordinate to the canonical specifications. | [`SLICE1_DATA_MODEL.vi.md`](./SLICE1_DATA_MODEL.vi.md) |

## Deployment and operations

| Document | Purpose | Vietnamese |
| --- | --- | --- |
| [`DEPLOYMENT.md`](./DEPLOYMENT.md) | The starting point for running PartFlow outside a workstation: current status, the production stack contract, go-live requirements, and release naming and publication. | [`DEPLOYMENT.vi.md`](./DEPLOYMENT.vi.md) |
| [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) | Installing, updating, backing up and monitoring PartFlow production on the company Synology NAS, plus the optional restricted staging. | [`deployment/SYNOLOGY_NAS.vi.md`](./deployment/SYNOLOGY_NAS.vi.md) |
| [`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md) | The Deployment Admin tool (`pf`) that manages Synology staging instances, including its planned design as a reusable deployment controller. | [`deployment/SYNOLOGY_ADMIN.vi.md`](./deployment/SYNOLOGY_ADMIN.vi.md) |
| [`deployment/VPS.md`](./deployment/VPS.md) | Running the same production stack on a Linux virtual private server (VPS), the alternative production host. | [`deployment/VPS.vi.md`](./deployment/VPS.vi.md) |
| [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md) | Why shared web hosting is only conditionally possible and what a provider must confirm first, including the Hawk Host finding. | [`deployment/SHARED_HOSTING.vi.md`](./deployment/SHARED_HOSTING.vi.md) |
| [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) | The operator's procedures: deployment records, health checks, backups, restore tests, releases, rollback, reconciliation, incidents and the routine schedule. | [`deployment/OPERATIONS_RUNBOOK.vi.md`](./deployment/OPERATIONS_RUNBOOK.vi.md) |

The folder `deployment/Synology DSM7 - Staging/` holds a superseded staging kit kept
for reference only; it is never current instructions.

## Translation policy

[`TRANSLATION_POLICY.md`](./TRANSLATION_POLICY.md) defines how Vietnamese
mirrors are derived, reviewed, marked, and updated. The files in `docs/archive/`
are historical superseded material and are intentionally not translated. HTML
mockups are executable visual references rather than documentation mirrors and
are also intentionally excluded.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Rewritten for new readers: an introduction, a current one-sentence description of each document, and this change table. |
| 2026-10-14 | `2c360cb` | Deployment Admin entry mentions its planned reusable-controller design goal. |
| 2026-09-02 | `8e5ab18`, `6fae517` | Vietnamese mirrors moved beside their English sources and every link repointed. |
| 2026-09-02 | `d2f68de` | Index created with the deployment guides and the Vietnamese mirrors. |
