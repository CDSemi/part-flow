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

Read them in this order: `DEPLOYMENT.md` §1 and §2 first, then the installation guide of your host, then the runbook
when PartFlow runs.

| Document | Purpose | Vietnamese |
| --- | --- | --- |
| [`DEPLOYMENT.md`](./DEPLOYMENT.md) | The starting point: what production consists of, which document to follow for each task, the terms and safety rules, the reference of the production stack, the go-live requirements, and release naming and publication. | [`DEPLOYMENT.vi.md`](./DEPLOYMENT.vi.md) |
| [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) | Installing PartFlow production on the company Synology NAS step by step, from an empty NAS to HTTPS, first-run setup, backups and monitoring. | [`deployment/SYNOLOGY_NAS.vi.md`](./deployment/SYNOLOGY_NAS.vi.md) |
| [`deployment/SYNOLOGY_PUBLIC_HTTPS.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.md) | Optional: making PartFlow on the NAS reachable from the Internet through Cloudflare, with the router's port forwarding and dynamic DNS. | [`deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md) |
| [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) | Running PartFlow once installed: health checks, backups, restore drills, new releases, rollback and restore, reconciliation, common problems and incidents, and the routine schedule. | [`deployment/OPERATIONS_RUNBOOK.vi.md`](./deployment/OPERATIONS_RUNBOOK.vi.md) |
| [`deployment/VPS.md`](./deployment/VPS.md) | Installing the same production stack on a Linux virtual private server (VPS), the alternative production host. | [`deployment/VPS.vi.md`](./deployment/VPS.vi.md) |
| [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md) | Why shared web hosting is only conditionally possible and what a provider must confirm first, including the Hawk Host finding. | [`deployment/SHARED_HOSTING.vi.md`](./deployment/SHARED_HOSTING.vi.md) |

Not needed for production: [`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md)
([Vietnamese](./deployment/SYNOLOGY_ADMIN.vi.md)) describes the Deployment Admin tool (`pf`), which manages staging
instances on a host other than the production one, including its planned design as a reusable deployment controller.

## Translation policy

[`TRANSLATION_POLICY.md`](./TRANSLATION_POLICY.md) defines how Vietnamese
mirrors are derived, reviewed, marked, and updated. The files in `docs/archive/`
are historical superseded material and are intentionally not translated. HTML
mockups are executable visual references rather than documentation mirrors and
are also intentionally excluded.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Deployment documents listed in reading order with the Cloudflare guide; the Deployment Admin guide marked as not needed for production; the superseded staging kit removed. |
| 2026-11-09 | `398c617` | Rewritten for new readers: an introduction, a current one-sentence description of each document, and this change table. |
| 2026-10-14 | `2c360cb` | Deployment Admin entry mentions its planned reusable-controller design goal. |
| 2026-09-02 | `8e5ab18`, `6fae517` | Vietnamese mirrors moved beside their English sources and every link repointed. |
| 2026-09-02 | `d2f68de` | Index created with the deployment guides and the Vietnamese mirrors. |
