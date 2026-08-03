# PartFlow Implementation Roadmap

> **Authority:** Canonical for implementation order, phase boundaries, dependencies, and temporary limitations.
> Domain behavior and product scope are defined by [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md); the approved target UI by [`GUI_DESIGN.md`](./GUI_DESIGN.md).

## Current State

- Canonical project specification: `PROJECT_PROFILE.md` v10 (Area Completion — user-facing DONE, canonical `AREA_COMPLETED` Movement, derived `READY_TO_TRANSFER` holding state, quantity-scoped and distinct from `RELEASED_FROM_MACHINE` and `STOCKED`, with atomic implicit source completion during transfers and whole-command Undo; plus the v9 rules: Request Types `NEW`/`MODIFY` only; Repair as an explicit movement intent `TRANSFERRED · movement_reason REPAIR`; Floating Routes as the default route mode with optional AssignedRoute; nullable external Work Order Numbers rendered `—` — no temporary number generation; PN barcodes carrying the PN with create-on-first-use and case-insensitive identity; canonical `SCRAPPED` and auditable `QUANTITY_ADJUSTED · INCREASE` events; two Area ownership modes with one-shot Machine assignment and no Machine sessions; per-station Scan Station routing; administrative archival/purge maintenance policy; nullable due dates, canonical demand ordering, server-confirmed-write scan rule, confirmation before Hot reordering).
- Approved target UI: `GUI_DESIGN.md` v11, with mockup v11 (`docs/mockups/partflow-gui-mockup-v11.html`) as its visual reference.
- Phase 1 — Repository Foundation source code exists: React + TypeScript frontend, FastAPI backend, PostgreSQL, Alembic (no-op baseline), Docker Compose, the `/api/health` endpoint, formatter/linter/type-check/test tooling, and CI.
- Phase 2 — Frontend Design System and Application Shell is implemented: semantic design tokens with switchable Dark/Light themes (Dark default), URL-based routing and navigation (Management remembers its last-used sub view per session), all seven approved GUI views as mock views behind a real development-only build boundary (mock views and datasets are loaded only when `import.meta.env.DEV` is true; production builds render an explicit not-connected state per route, verified by a mock-sentinel check in `npm run build`), loading/empty/error/disconnected/long-data states with a dev-only `?state=` preview, and the real `/api/health` connectivity integration with **fast connectivity detection** (GUI_DESIGN §3.6): browser online/offline events, ~1 s health polling with a request timeout below the probe interval and no overlapping probes, recheck on tab focus/visibility, passive probes never flipping the UI to a "connecting" state, persistent OFFLINE banner with Retry, write controls disabled while disconnected, and Scan Station input re-enable/refocus on recovery — no WebSocket/SSE and no offline write queueing. The views implement the approved GUI v9 interaction model against local mock state: the Work Orders view (route `/management/work-orders`) with New Work Order as a modal dialog over the WO list, optional WO Number and due dates (a blank number is saved as NULL and renders `—` — no temporary number generation), the manual-first multi-step Add Part dialog with PN-carrying barcodes and create-on-first-use as a secondary method, the save-omission confirmation, OPEN Work Order demand-line adding/removal per the Work Order Demand removal rule (PROJECT_PROFILE §13), native calendar date inputs, mock validation, and unsaved-change protection; the Production Board with live header clock, the shared Hot presentation (`🔥#n` before the PN), urgency-text-only blinking, scrap visibility on the total line, Job-Numbers-last column order, and dynamic height-measured pagination; the Scan Station as a per-station route (`/scan-station/:stationId` behind a Station Selector) with PN-centric one-shot wizards that end in dedicated structured confirmation views and record nothing before the final confirmation (three-step intake with MODIFY/FLOATING defaults, source-explicit transfers, three-step one-shot Machine assignment with Step-1 barcode selection and no Machine session, auditable quantity addition, explicit Repair intent, the PF:SCRAP counting workflow, Undo with the shared summary-confirmation presentation), an ENTER-button-free Scan Barcode card with in-row manual PN entry, floating auto-dismissing scan notifications (the OFFLINE banner stays persistent), grid-layout PN rows with a separated action rail (`{n} scrapped` text only, `QUEUE` only on Machine cards), and Area statistics in the header; the Area Board per-Area detail and the Scan Station sharing one Area/Machine monitoring layout built from shared presentation components; and Priority Management with confirmation before every order change. The GUI v10 refinements are implemented against development-only mock state: the Area completion state (manual DONE from Machine-card rows — the distinct `DONE`/`QUEUE` actions — and from the direct-processing action dialog; the `Finished — ready to move` group; implicit `AREA_COMPLETED` + `TRANSFERRED` as one atomic mock command with whole-command Undo), keyboard-wedge scan capture at the Scan Station, the restructured header (identity + Worker Session row, Operations chips, reconciling semantic totals incl. Done), the statistics-free `In this Area now` card, the Production Board's explicit Area/Machine/state location model (`on mch.` Machine chips, distinct `done` rows, separated scrap line, enforced PN minimum width, rebalanced column widths), the Area Board's shared-row All Areas overview with finished-state distinction, Tracking's sibling route arrows plus `AREA_COMPLETED` history and ready-to-transfer presentation, the Work Order Details modal dialog over the list with the component-owned QuantityKeypad stylesheet, the redesigned Priority ranking confirmation (moved-item summary, current-vs-proposed comparison, `Apply ranking`), the professional operator-copy pass with the standard `Cancel (Esc)` label, and a mock Scan Station state machine so confirmed commands update the monitoring surfaces. The GUI v11 refinements complete the Phase 2 presentation: the Scan Station production mode route `/scan-station/:stationId/production` (top application navigation hidden — presentation only, never a security boundary; standard mode unchanged for Manager/Admin use), Station Selector cards with Operation chips and the explicit `Open` / `Open production mode` actions, the Production Board's board-owned Live heartbeat, No.-column Hot flame, intrinsic 15ch PN width, `Location | Quantity | State/activity | Time` row alignment with full Machine names and External activity chips, the continuous total separator with the same-line scrap chip, manual page controls with ArrowLeft/ArrowRight, the Priority two-snapshot ranking confirmation (`Current Position` → `New Position`, explicit WO/Job metadata chips, `Not listed` add/remove placeholders), and the exhaustive rendered-copy audit with its automated guard (`src/rendered-copy.test.ts`). The canonical Purchase Order → Work Order vocabulary migration (PROJECT_PROFILE v7, GUI_DESIGN §12.5) is applied across current documentation, frontend code, routes, and tests. Phase 2 remains frontend presentation with development-only mock behavior: no domain implementation, no database migrations, no backend business APIs, and no persisted production writes exist yet.

## Implementation Principles

- Build in vertical slices; complete one workflow end to end before expanding.
- Never transfer quantity before the system has a real quantity-introduction workflow: Work Order Intake and production release precede all real transfer workflows.
- Preserve Presentation → Application → Domain → Infrastructure.
- Use mock data only until the relevant backend slice exists; mock behavior never leaks into production.
- Ambiguity always requires explicit confirmation before any write; unknown or invalid input is rejected with no write.
- Production writes are blocked while disconnected; offline scan synchronization is deferred and unapproved.
- Do not implement ERP integration during MVP.

## Phase 1 — Repository Foundation

Scope:

- React + TypeScript frontend
- FastAPI backend
- PostgreSQL
- Alembic
- Docker Compose
- health endpoint
- frontend/backend/database connectivity
- formatter, linter, type checks, and test foundations

Completion criteria:

- Development environment starts from documented commands.
- Frontend calls the backend health endpoint; backend connects to PostgreSQL.
- Formatting, linting, type checks, and initial tests run successfully.

## Phase 2 — Frontend Design System and Application Shell

Scope:

- shared tokens
- dark and light contexts
- application routing/navigation
- approved mock views, including the Work Orders view in the application shell (New Work Order modal with optional WO Number/due dates, manual-first Add Part flow, OPEN Work Order line editing, calendar date inputs, mock validation, unsaved-change protection)
- fast connectivity detection: browser online/offline events plus ~1 s `/api/health` polling with focus/visibility recheck (WebSocket/SSE remain out of scope and deferred)
- development-only mock data behind a real production build boundary (mock-sentinel verification in the build)
- loading, empty, error, connectivity-loss, and long-data states

Phase 2 is frontend presentation plus development-only mock behavior.

Non-goals:

- production business rules in mock components
- domain implementation or database migrations
- backend business APIs
- persisted production writes (every Phase 2 save changes development-only mock state)
- ERP integration

## Phase 3 — Minimum Canonical Domain and Data Foundation

Only the foundation required by manual Work Order Intake and production release:

- Department
- Area
- Operation
- PartNumber (case-insensitive unique identity, create-on-first-use — no preloaded catalog requirement)
- WorkOrder (nullable external `work_order_number`; uniqueness for non-null numbers only)
- WorkOrderDemand (`request_type IN ('NEW','MODIFY')`)
- RouteTemplate
- RouteStep
- AssignedRoute (optional — only `PLANNED` flows carry one)
- QuantityFlow with `route_mode` (`FLOATING` default / `PLANNED`) and nullable `assigned_route_id` — **the data foundation must support Floating Routes from the start**
- PartMovement (including the typed `movement_reason` column concept for later Repair movements)
- derived current-position projection

Rules:

- Preserve canonical PartMovement field names when those fields are introduced: `station_id`, `occurred_at`, `server_received_at`, `device_event_id`, `movement_reason`.
- Do not introduce competing names such as `client_event_id`.
- Movement history is immutable; quantity integrity is enforced; domain invariants are covered by tests.
- Floating Route traces are derived from Movement history — no second mutable route history table.

## Phase 4 — Manual Work Order Intake and Production Release

The first business vertical slice, presented in the UI as the Work Orders view (GUI_DESIGN §11). It must:

- create/find WorkOrder,
- save a confirmed blank external WO Number as `NULL` on an internal Work Order (rendered `—`, never persisted as a placeholder; replaceable later through an audited edit — PROJECT_PROFILE §7 Work Order; no temporary number is generated),
- accept null WorkOrder and WorkOrderDemand due dates as valid data (PROJECT_PROFILE §8.2/§8.3),
- create/find PartNumber (case-insensitive, create-on-first-use),
- create/update WorkOrderDemand,
- save demand separately from production quantity,
- explicitly release production,
- create QuantityFlow with its route mode (`FLOATING` default),
- snapshot an independent Route **only** for a `PLANNED` release — a Floating release has no AssignedRoute,
- append `RECEIVED`,
- establish current position,
- remain transactional and idempotent,
- not automatically merge with existing active quantity,
- enforce the Work Order Demand removal rule (PROJECT_PROFILE §13) transactionally in the Application/Domain layer: a WorkOrderDemand line may be deleted only while no production quantity has been released for it; once released, deletion must be refused, and removal must never cascade to the PartNumber master, QuantityFlows, PartMovements, release history, or other WorkOrderDemand records for the same PN.

Manual entry comes before file import. Development seed data may support UI development and tests, but seeded `RECEIVED` fixtures are not the product intake workflow.

## Phase 5 — Scan Station Transfer to an Area Queue

Pilot example: `Material -> Lathe queue`, where Lathe is configured as `Queue -> select Machine by scan`.

The slice must include:

- stable Scan Station configuration bound to Lathe,
- PN barcode resolution,
- source QuantityFlow resolution,
- Operation resolution,
- route validation,
- quantity validation,
- explicit ambiguity confirmation,
- immutable `TRANSFERRED` Movement,
- transactional current-position projection update,
- idempotent retry handling,
- keyboard focus restoration,
- recent scans and Area inventory refresh.

Candidate resolution must respect current position, route, Operation, station context, and valid deviations — never "every active flow outside the target Area".

Temporary limitation: full-QuantityFlow movement only. Partial movement must not be claimed as supported before SPLIT (Phase 8); partial input is refused clearly with no write. The target GUI requirement for partial quantity (GUI_DESIGN §4.7) remains. Second temporary limitation: until Area completion exists (`AREA_COMPLETED` — Phase 6 for Machine Areas, Phase 7 for direct processing), Phase 5 transfers record `TRANSFERRED` alone; from the phase that introduces Area completion onward, a transfer from actively processing quantity appends `AREA_COMPLETED` + `TRANSFERRED` as one atomic application command (PROJECT_PROFILE §8.11).

## Phase 6 — One-Shot Machine Assignment and Area Completion

- Machine barcode resolution (one-shot shortcut only — **no Machine session of any kind**)
- Machine/Area validation
- Machine-first (Machine scan opens the one-shot assignment dialog with the Machine preselected) and PN-first (assign action on queued quantity) entry points
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE` (the `QUEUE` return action — returns unfinished or paused quantity to the queue, never completed work)
- **`AREA_COMPLETED` for Machine Areas** (PROJECT_PROFILE §7 Area Completion, §8.11, §12): the manual DONE action on Machine-assigned quantity (`DONE` and `QUEUE` stay two distinct actions), deriving `READY_TO_TRANSFER`, clearing `current_machine_id`, keeping the Area as location; a transfer from `ON_MACHINE` quantity appends `AREA_COMPLETED` + `TRANSFERRED` in one transaction; DONE is quantity-scoped and never a PN status, never `STOCKED`
- inactive Machine rejection
- one Machine may hold quantities of multiple PNs; one Machine behaves exactly like several (never auto-assign by Machine count)

## Phase 7 — Direct Area Processing (Areas Without Machines)

- Area without Machines takes direct processing ownership (no queue)
- Operation recorded and Machine null
- multiple-Operation confirmation
- **`AREA_COMPLETED` for direct processing**: the same manual DONE workflow without a Machine field, and implicit completion when directly processing quantity transfers to another Area (one atomic `AREA_COMPLETED` + `TRANSFERRED` command)
- exactly two Area modes exist: no Machines → direct processing; Machines → `QUEUE_AND_ASSIGN` (PROJECT_PROFILE §12) — no per-Area assignment-mode configuration and no single-Machine auto-assignment

## Phase 8 — Quantity SPLIT and MERGED Workflows

- partial movement
- preserved lineage
- quantity conservation
- `SPLIT` and `MERGED` Movement history
- quantity keypad supports valid partial amounts
- **SPLIT is the prerequisite for partial Repair** (Phase 9): before SPLIT exists, partial Repair is refused clearly with no write — it must not be claimed as supported

## Phase 9 — Undo, Corrections, and Auditable Quantity Events

- `REVERSED` Movement; Undo reverses the complete application command (never one arbitrary row of a multi-Movement action — e.g. an implicit `AREA_COMPLETED` + `TRANSFERRED` transfer reverses as one), with a summary confirmation before applying
- original Movement preserved
- authorization
- reason when configured
- projection restoration
- audit visibility
- **Repair movements**: `TRANSFERRED · movement_reason REPAIR` with mandatory reason; full-flow Repair from this phase, partial Repair only on top of SPLIT (Phase 8)
- **Scrap**: canonical `SCRAPPED` events from the PF:SCRAP counting workflow — one auditable operation per confirmation; reconciliation `introduced = active + stocked + scrapped`
- **Quantity additions**: `QUANTITY_ADJUSTED · direction INCREASE` with mandatory reason, never altering requested quantity

None of these backend workflows exist yet — Phase 2 only demonstrates the approved interaction against mock state.

## Phase 10 — Stockroom and WorkOrderAllocation

- `STOCKED` Movement
- available stocked quantity
- suggested allocation follows the canonical demand ordering (PROJECT_PROFILE §18):
  1. highest manager-defined Work Order Demand priority
  2. within the same priority: dated demand earliest-first; undated demand after all dated demand, ordered by parent WorkOrder received_date (oldest first)
- equal values resolved by a stable deterministic tie-breaker (implementation detail, not a business rule)
- WorkOrderAllocation recorded separately from PartMovement
- authorization for allocation adjustment

## Phase 11 — Read Models and Monitoring Views

- Production Board
- Area Board, including the Manager Summary content in its All Areas overview (no separate Manager Summary view)
- Tracking
- movement-derived projections
- stale-feed and long-data states

## Phase 12 — Priority Management

- Hot WorkOrderDemand ranking
- add/search/scan (adding at the bottom applies directly)
- reorder (drag-and-drop, Move Up / Move Down)
- confirmation before removal and before every order change of existing entries — including Undo and Redo (PROJECT_PROFILE §21 Priority Management)
- audited application after explicit confirmation
- Undo/Redo

## Phase 13 — Administration

- Departments
- Areas
- Operations
- Machines
- Workers
- Route Templates
- barcode configuration
- Worker session policies
- correction permissions

## Phase 14 — Authentication and Role Enforcement

Role-based authorization per PROJECT_PROFILE §19.

## Phase 15 — File-Based Work Order Import

- idempotent import
- row validation
- clear partial-failure reporting
- no ERP dependency

## Phase 16 — Deployment, Production Hardening, and Admin Maintenance

- backups
- migrations
- HTTPS/internal access
- observability
- rollback
- reconciliation checks
- pilot deployment
- **administrative archival/purge maintenance** (PROJECT_PROFILE §28): PN archive/soft-delete with `(archived)` history markers and a separate explicit physical purge; history archival & purge with retention policy / size threshold / manual triggers, scope preview, mandatory reason, and full audit — normal runtime stays append-only; no full retention engine is built before this phase

## Deferred

- ERP synchronization
- offline scan synchronization (not part of MVP; production writes stay blocked while disconnected)
- WebSocket/SSE push connectivity (event-driven + polled health detection is the approved mechanism)
- advanced analytics
- speculative automation
- broad ERP/MES features
