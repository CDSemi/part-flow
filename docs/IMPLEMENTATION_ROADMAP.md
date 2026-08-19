# PartFlow Implementation Roadmap

> **Authority:** Canonical for implementation order, phase boundaries, dependencies, and temporary limitations.
> Domain behavior and product scope are defined by [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md); the approved target UI by [`GUI_DESIGN.md`](./GUI_DESIGN.md).

## Current State

- Canonical project specification: `PROJECT_PROFILE.md` (the v19 badge-confirmation round — per-action final-confirmation gates for DONE / QUEUE return / Undo: a Worker badge scan as the last step after the confirmation summary in scanned-session Areas behind three Administration → Worker sessions options defaulting to on, a final toned confirmation question in fixed-Worker Areas, superseding the v18 "Undo needs no extra badge scan" rule; building on the v18 decision round closing the remaining GUI open questions — Workers as Scan-Station-scoped audit identity separate from Users, with profiles of stable id + name + badge barcode + avatar + active status and no employee number; Worker badge barcodes are the company's existing employee badges, exact-matched as non-`PF:` values with the former `PF:WORKER:` format removed; scanned Worker Sessions expiring through a configurable sliding inactivity timeout — Administration default with per-Area overrides, refresh on valid production interactions only, immediate Worker switch, and a Scan-Station-only blocking badge modal that preserves open dialog drafts; Undo Worker identity following the Area mode with the reversal recording the Worker active at confirmation; theme persistence per User and Scan Station with precedence User → Station → Dark default; deterministic Priority hot-add barcode resolution — one eligible demand adds directly, several require explicit selection; unlimited in-session Priority Undo/Redo depth; the Production Board confirmed Department-wide with per-Department rotation display settings; plus the v17 PN-identity model — the canonical PN string itself, uppercase and whitespace-free, is the stable domain identity; the PartNumber master is optional, hard-deletable current metadata; production records — WorkOrderDemand, QuantityFlow, PartMovement, Allocation — keep their own canonical PN value with no `part_number_id` surrogate linkage; plus the configurable Movement-history retention policy: export losslessly, verify, then purge — never the reverse; plus the earlier rules: Machine return-to-service — RETIRED → ACTIVE reactivation of the **same physical machine** on the same record with append-only `RETIRED`/`REACTIVATED` lifecycle audit events and a forward-only Area change when the machine moved while retired, blocked on reissued identity or active-name collisions; display names unique among the active Machines of one Area, with reuse across time/replacement still allowed; plus the v11 rules: Machine lifecycle with derived operational state — maintenance override wins, otherwise assigned quantity → Running, otherwise Idle, with a `state_changed_at` timestamp and derived elapsed-time display; retirement instead of hard deletion and replacement as retire + new record with a reusable display name; RouteTemplate presented as Planned Routes with archive-instead-of-delete for ever-used templates and no separate versioning; Machines, Route Templates, and PartNumber master metadata as permission-based production master data managed in Management (Machines, Planned Routes, Part Numbers), not Administration; plus the v10 rules: Area Completion — user-facing DONE, canonical `AREA_COMPLETED` Movement, derived `READY_TO_TRANSFER` holding state, quantity-scoped and distinct from `RELEASED_FROM_MACHINE` and `STOCKED`, with atomic implicit source completion during transfers and whole-command Undo; plus the v9 rules: Request Types `NEW`/`MODIFY` only; Repair as an explicit movement intent `TRANSFERRED · movement_reason REPAIR`; Floating Routes as the default route mode with optional AssignedRoute; nullable external Work Order Numbers rendered `—` — no temporary number generation; PN barcodes carrying the PN with create-on-first-use and case-insensitive identity; canonical `SCRAPPED` and auditable `QUANTITY_ADJUSTED · INCREASE` events; two Area ownership modes with one-shot Machine assignment and no Machine sessions; per-station Scan Station routing; administrative archival/purge maintenance policy; nullable due dates, canonical demand ordering, server-confirmed-write scan rule, confirmation before Hot reordering).
- Approved target UI: `GUI_DESIGN.md`, with the latest mockup (`docs/mockups/partflow-gui-mockup-v18.html`) as the visual reference.
- Phase 1 — Repository Foundation source code exists: React + TypeScript frontend, FastAPI backend, PostgreSQL, Alembic (no-op baseline), Docker Compose, the `/api/health` endpoint, formatter/linter/type-check/test tooling, and CI.
- Phase 2 — Frontend Design System and Application Shell is implemented: semantic design tokens with switchable Dark/Light themes (Dark default), URL-based routing and navigation (Management remembers its last-used sub view per session), all ten approved GUI views as mock views behind a real development-only build boundary (mock views and datasets are loaded only when `import.meta.env.DEV` is true; production builds render an explicit not-connected state per route, verified by a mock-sentinel check in `npm run build`), loading/empty/error/disconnected/long-data states with a dev-only `?state=` preview, and the real `/api/health` connectivity integration with **fast connectivity detection** (GUI_DESIGN §3.6): browser online/offline events, ~1 s health polling with a request timeout below the probe interval and no overlapping probes, recheck on tab focus/visibility, passive probes never flipping the UI to a "connecting" state, persistent OFFLINE banner with Retry, write controls disabled while disconnected, and Scan Station input re-enable/refocus on recovery — no WebSocket/SSE and no offline write queueing. The views implement the approved GUI v9 interaction model against local mock state: the Work Orders view (route `/management/work-orders`) with New Work Order as a modal dialog over the WO list, optional WO Number and due dates (a blank number is saved as NULL and renders `—` — no temporary number generation), the manual-first multi-step Add Part dialog with PN-carrying barcodes and create-on-first-use as a secondary method, the save-omission confirmation, OPEN Work Order demand-line adding/removal per the Work Order Demand removal rule (PROJECT_PROFILE §13), native calendar date inputs, mock validation, and unsaved-change protection; the Production Board with live header clock, the shared Hot presentation (`🔥#n` before the PN), urgency-text-only blinking, scrap visibility on the total line, Job-Numbers-last column order, and dynamic height-measured pagination; the Scan Station as a per-station route (`/scan-station/:stationId` behind a Station Selector) with PN-centric one-shot wizards that end in dedicated structured confirmation views and record nothing before the final confirmation (three-step intake with MODIFY/FLOATING defaults, source-explicit transfers, three-step one-shot Machine assignment with Step-1 barcode selection and no Machine session, auditable quantity addition, explicit Repair intent, the PF:SCRAP counting workflow, Undo with the shared summary-confirmation presentation), an ENTER-button-free Scan Barcode card with in-row manual PN entry, floating auto-dismissing scan notifications (the OFFLINE banner stays persistent), grid-layout PN rows with a separated action rail (`{n} scrapped` text only, `QUEUE` only on Machine cards), and Area statistics in the header; the Area Board per-Area detail and the Scan Station sharing one Area/Machine monitoring layout built from shared presentation components; and Priority Management with confirmation before every order change. The GUI v10 refinements are implemented against development-only mock state: the Area completion state (manual DONE from Machine-card rows — the distinct `DONE`/`QUEUE` actions — and from the direct-processing action dialog; the `Finished — ready to move` group; implicit `AREA_COMPLETED` + `TRANSFERRED` as one atomic mock command with whole-command Undo), keyboard-wedge scan capture at the Scan Station, the restructured header (identity + Worker Session row, Operations chips, reconciling semantic totals incl. Done), the statistics-free `In this Area now` card, the Production Board's explicit Area/Machine/state location model (compact Machine chips — with the state wording later expanded to the full `on machine` by GUI v13 — distinct `done` rows, separated scrap line, enforced PN minimum width, rebalanced column widths), the Area Board's shared-row All Areas overview with finished-state distinction, Tracking's sibling route arrows plus `AREA_COMPLETED` history and ready-to-transfer presentation, the Work Order Details modal dialog over the list with the component-owned QuantityKeypad stylesheet, the redesigned Priority ranking confirmation (moved-item summary, current-vs-proposed comparison, `Apply ranking`), the professional operator-copy pass with the standard `Cancel (Esc)` label, and a mock Scan Station state machine so confirmed commands update the monitoring surfaces. The GUI v11 refinements complete the Phase 2 presentation: the Scan Station production mode route `/scan-station/:stationId/production` (top application navigation hidden — presentation only, never a security boundary; standard mode unchanged for Manager/Admin use), Station Selector cards with Operation chips and the explicit `Open` / `Open production mode` actions, the Production Board's board-owned Live heartbeat, No.-column Hot flame, intrinsic 15ch PN width, `Location | Quantity | State/activity | Time` row alignment with full Machine names and External activity chips, the continuous total separator with the same-line scrap chip, manual page controls with ArrowLeft/ArrowRight, the Priority two-snapshot ranking confirmation (`Current Position` → `New Position`, explicit WO/Job metadata chips, `Not listed` add/remove placeholders), and the exhaustive rendered-copy audit with its automated guard (`src/rendered-copy.test.ts`). The GUI v12 refinements polish the Phase 2 presentation further: cursor-aware shared Quantity NumPad editing (one selection/caret model for the physical keyboard and the on-screen keypad), a shell layout that derives the content area from flex layout (`100dvh` + fallback) so fitting views show no false vertical scrollbar, the compact production-mode Dark/Light control in the Scan Station header-actions group, the dialog information hierarchy (distinct description / recap-with-entity-chips / marked guidance kinds / strongest validation) with the confirmation-summary row emphasis, the Production Board's coherent time-first clock, the subtle Hot-flame pulse with its reduced-motion fallback, rebalanced column shares with heading-derived Due Date / Total Days minimums, shared cross-row location tracks, the flex-anchored footer with the user-facing sorting legend (`Order: Hot rank first → earliest due date → no due date by oldest received date.`), and the Priority `#old → #new` rank transitions with explicit `Not listed` add/remove sides. The GUI v13 refinements continue the Phase 2 presentation polish: the Station Selector's full-card standard-mode selection with a separate `Production mode` action, the unified non-interactive Scan Station footer (Station ID, mode label, `Ctrl+Shift+K: switch mode` hint) in both modes, the secondary-toned `Total pcs` statistic, the vertical connectivity/theme stack with a borderless compact theme control in the production-mode header, touch-primary quantity inputs (`inputMode="none"` plus a `visualViewport` NumPad-collapse fallback), the Production Board's full `on machine` wording with semantic state-text tones and plain error-toned scrap text, the deadline-synchronized page-rotation indicator, the restrained footer aggregate summary, the addressable Production Board Kiosk mode (`/production-board/kiosk`, `Ctrl+Shift+K`, board-owned kiosk header, hidden application navigation), Tracking's toggleable/closable detail panel with a full-width no-selection state, the Work Orders copy cleanup and responsive demand-line card layout with near-full-screen narrow-viewport dialogs, the Priority confirmation's shared snapshot grid tracks and separated Action emphasis, and the honest Administration placeholders distinguishing the Phase 2 preview, the Phase 3.5 minimum environment setup, and full Administration (Phase 13). The GUI v14 changes continue the Phase 2 presentation: the two new Management sub views as mocks — Machines (`/management/machines`: active-Machines table with derived operational state and elapsed time in state from `stateChangedAt`, maintenance override with note/expected return, retirement blocked while quantity is assigned, the Retired Machines table (action-less until GUI v15), the New/Edit Machine dialog with a fixed Area for existing Machines and optional asset metadata) and Planned Routes (`/management/planned-routes`: searchable RouteTemplate list with Active/Archived status, usage dialog, future-assignments-only edit note, archive-instead-of-delete for ever-used templates, delete for never-used only) — making the Management sub-view order Area Board · Machines · Tracking · Work Orders · Planned Routes · Priority (both new views permission-based production master data, deliberately not Administration, which keeps no duplicate screens); the shared `pf-heartbeat` connected-dot animation behind the `ONLINE` chip and the Production Board `Live` status; the kiosk header showing the same `Live` status with an explicit `Exit kiosk` button instead of the ONLINE chip; the ~20 % tighter Production Board location-grid track minimums; Tracking's whole-row selection with the modeless floating detail overlay (no table reflow on open/close); the content-sized Priority snapshot rank track; the Scan Station's `inputMode="none"` main barcode input on touch-primary devices via the shared `touch-device.ts` module, the header-card-language actions group, the content-sized flattened confirmation summaries, the operator-facing intake copy, and the `Assign to Machine` dialog naming; the shared monitoring cards' state age (`running · 1h 24m`) and maintenance context; and the Administration sidebar without Machines/Route Templates sections. The GUI v15 changes (later superseded as current by GUI v16–v18 and post-v18 refinements — GUI_DESIGN.md §15, PROJECT_PROFILE.md v17 — including the Part Numbers management view and its route `/management/part-numbers`, and the Manager Summary view merging into Area Board's All Areas overview): the Machine return-to-service lifecycle (PROJECT_PROFILE v12) as mock behavior — the Machines view's `Machine | State | Assigned now | Asset | Maintenance` columns with the per-row Maintenance On/Off switch (opening the existing dialogs) and whole-row Edit activation, the Edit dialog's append-only lifecycle audit list and Danger-Zone Retire with a typed identifier confirmation (Asset Tag, else barcode), and the Retired Machines table's per-row `Reactivate…` dialog (same-physical-machine checkbox, required reason, identity-reissue blockers, active-name-uniqueness rename, forward-only Area change, returning Idle, appending `REACTIVATED`); the shared `TypedConfirmDialog` / `UnsavedChoiceDialog` primitives with `Cancel (Esc)` compliance in the Machines/Routes dialogs; the Planned Routes active/archived table split with Area-colored step chips, whole-row editing, in-dialog Duplicate/Archive…/Delete…, drag-and-drop reordering with kept Up/Down controls, Area-scoped Operation selects and preferred-Machine selects by stable Machine id (`preferredMachineId` matching `preferred_machine_id`; explicit `(unavailable)` values, never silently cleared), and the typed archive-confirmation flow; the four-kind guidance taxonomy (marker-less neutral removed), the restructured intake steps, semantic confirmation-summary value tones with Area dots, operator-only scrap copy, and the sharpened offline copy family; the `.ss-headgroup` production-mode header; the Production Board's proportional, configurable page rotation (`ROTATE_MS_PER_ROW` / `ROTATE_MS_MIN` — 3 s per displayed row, 6 s minimum — with the indicator sharing deadline and duration), the unified three-zone kiosk header with the `Exit kiosk` control moved to the footer controls row, and the neutral total-quantity tones; the full-width Work Orders list with one search/New toolbar row, whole-row activation and dialog heading polish; and the Priority snapshot divider between the shared position track and the PN column. The canonical Purchase Order → Work Order vocabulary migration (PROJECT_PROFILE v7, GUI_DESIGN §15.10) is applied across current documentation, frontend code, routes, and tests. Phase 2 remains frontend presentation with development-only mock behavior: it contains no domain implementation, no backend business APIs, and no persisted production writes — every Phase 2 save changes development-only mock state.
- Phase 3 — Minimum Canonical Domain and Data Foundation is implemented: the framework-independent domain vocabulary in `backend/app/domain/` (canonical PN normalization — trim surrounding whitespace, reject internal whitespace, canonicalize to UPPERCASE, with `InvalidPartNumberError` — and the `RequestType` (`NEW`/`MODIFY`), `RouteMode` (`FLOATING`/`PLANNED`), `MovementType` (`RECEIVED` only) and `QuantityFlowStatus` (`ACTIVE` only) enumerations, each widening additively in the phase that introduces new values), the SQLAlchemy schema mappings in `backend/app/infrastructure/models.py`, and the Alembic migration `0002_phase3_domain` creating the twelve canonical tables with explicit deterministic constraint and index names: `departments`, `areas`, `operations`; the optional `part_numbers` master with the canonical PN string as its natural primary key (canonical-form CHECK, no surrogate id, and **no foreign key from any production table** — the master stays hard-deletable per PROJECT_PROFILE §8.1/§28); `work_orders` with a nullable external Work Order Number and a partial unique index over non-null numbers only; `work_order_demands` (canonical-PN CHECK, `request_type IN ('NEW','MODIFY')`, positive requested quantity, nullable due date, `priority_rank`, Job Numbers as a plain text array — metadata, never an aggregate); `route_templates`/`route_steps` (no version column — snapshots preserve history); the independent `assigned_routes`/`assigned_route_steps` snapshot tables (no `quantity_flow_id` back-reference); `quantity_flows` with `route_mode` (`FLOATING` default / `PLANNED`), `assigned_route_id` unique and present exactly when `PLANNED` (CHECK), the `current_area_id NOT NULL` current-position projection column, and the `(id, part_number)` composite-FK target; and the append-only `part_movements` event table (movement-type CHECK currently `RECEIVED` only, `RECEIVED` shape check `from_area_id IS NULL`, positive quantity, unique `device_event_id` idempotency key, composite FK `(quantity_flow_id, part_number)` → `quantity_flows (id, part_number)` so a Movement can never carry another flow's PN, and a statement-level raise-on-write trigger rejecting UPDATE, DELETE, and TRUNCATE in PostgreSQL itself). Constraint-level integration tests (`backend/tests/test_phase3_schema.py`) run the real migrations upgrade → downgrade → upgrade against dedicated temporary databases and verify these invariants, models↔migration metadata parity, and the absence of every deferred later-phase column and table; `backend/tests/test_part_number_normalization.py` covers the canonical PN rules. Phase 3 is domain and data foundation only: **no business API, no intake or release workflow, and no production write path exists yet** — the schema is migrated but nothing writes production data. The generic `audit_events` table belongs to the Phase 4 slice migration (SLICE1_DATA_MODEL §16–§17); `scan_stations`, `machines`, and the other environment configuration arrive with Phase 3.5; the additional application-role UPDATE/DELETE revocation on `part_movements` arrives with deployment hardening once a distinct application database role exists (the trigger already enforces append-only for every non-superuser path).
- Phase 3.5 — Minimum Environment Setup is implemented end to end (persistence, APIs, and the real frontend wiring). Persistence: the Alembic migration `0003_phase35_environment` extends `areas` (description, color, terminal flag, the server-assigned stable `PF:AREA:<id>` barcode guarded by an assign-once trigger) and `operations` (description, optional expected duration, external flag), and creates `scan_stations` (stable URL-safe Station ID as the natural key, Area binding, active flag — no station barcode), `machines` (immutable auto-assigned Asset Tags guarded by triggers, per-Area active display-name uniqueness, the explicit maintenance override, `state_changed_at`, retirement dates), the append-only `machine_lifecycle_events` history (`RETIRED`/`REACTIVATED` with nullable reference-free actor, reason, before/after state, and the previous → current Area pair on a move while retired), and the singleton Machine Asset Tag format configuration with its persisted never-reuse counter. APIs: the environment configuration surface (`/api/departments`, `/api/areas`, `/api/operations`, `/api/scan-stations`, and `GET`/`PUT /api/barcode-configuration/machine-asset-tag-format` — create/read/update and deactivate only, no DELETE endpoints, server-owned fields rejected via `extra="forbid"`) and the Machines management surface (`/api/machines` — listing, creation with server-side Asset Tag allocation taking the previewed `expected_asset_tag` as an optimistic precondition only, the single Save-changes PATCH transaction covering metadata plus the in-place maintenance context, the maintenance override start/clear sub-resource, and retirement/reactivation each committing atomically with its lifecycle event, a retirement optionally carrying the recorded Save decision of the Edit dialog's unsaved edits), with Application-layer services owning every rule and transaction and typed errors mapped centrally to HTTP responses. Frontend: Administration's five minimum-environment sections (Departments, Areas, Operations, Scan Stations, Barcode configuration) and Management → Machines are REAL views reading and writing this API — the approved GUI v18 interaction model unchanged, mock state replaced by server state with explicit loading/error/retry presentation, server-rejected writes surfaced in place, and the offline write-block still gating every write action. The production build boundary was restructured for this: the real views live in `src/app/real-views.ts` and ship in every build alongside the shared API client layer (`src/api/`), while the remaining Phase 2 mock views stay behind the development-only registry (`src/app/dev-views.ts`) — `src/mocks/` never enters a production bundle (sentinel-scan on build plus a source-level no-mock-import guard over the production modules in `src/production-boundary.test.ts`); the development-only Worker sessions policy preview is reachable solely through an `import.meta.env.DEV`-guarded lazy import, and every not-yet-real Administration section presents itself honestly as arriving with full Administration (Phase 13). Still deliberately absent (per this phase's scope): no production workflow and no production write of any kind, no Machine assignments (Running/assigned-quantity presentation activates with the Phase 6 workflows — until then active Machines are Idle unless Maintenance overrides), no real Planned Routes/Part Numbers/Workers/Users/Worker-session policies, no `audit_events`, and per-Area Worker ID modes arrive with the Worker-session workflows (the Areas table shows the column unconfigured).

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
- PartNumber master as optional current metadata keyed by the canonical PN string (uppercase, whitespace-free normalization; create-on-first-use — no preloaded catalog requirement; production tables keep their own canonical PN value and never reference the master by FK)
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

## Phase 3.5 — Minimum Environment Setup (named prerequisite — no later phases renumbered)

The smallest administration capability required to operate the initial
production slices, deliberately separated from the later full Administration
phase (Phase 13). It sits after the domain foundation (Phase 3) and **before
any real production workflow needs a configured environment**: Phase 4 needs
configured Departments, Areas and Operations for release, and Phase 5 needs
active, Area-bound Scan Stations.

Scope — configuration management for exactly:

- Departments
- Areas (identity, color/display properties, terminal flag)
- Operations (per Area)
- Scan Stations (Station ID, bound Area, active status)
- Machines (per Area, lifecycle and maintenance status) — configured through
  **Management → Machines** (GUI_DESIGN §12), permission-based for authorized
  production roles rather than an Administration panel, but still part of this
  minimum setup prerequisite: Machines must be configured before real
  production runs
- Machine lifecycle event history — the dedicated append-only
  **`machine_lifecycle_events`** table (canonical name) persisting the
  `RETIRED` and `REACTIVATED` lifecycle events PROJECT_PROFILE §8.6 requires,
  created in this phase together with `machines`. A retirement or
  reactivation and its lifecycle event commit atomically — one transaction,
  no lifecycle change without its event and no event without its change —
  and recorded events are immutable/append-only. Each event records the
  canonical lifecycle context: event type, Machine identity, occurred time,
  reason, before/after state, and the previous and current Area when the
  Machine moved while retired. This is Machine lifecycle/history
  persistence, deliberately **not** a generic audit framework and **not**
  the Slice 1 `audit_events` mechanism: `audit_events` stays owned by
  Phase 4 and scoped to WorkOrder, WorkOrderDemand, and PartNumber
  (SLICE1_DATA_MODEL §16) — Machine never becomes an `audit_events` entity
  type, and Phase 3.5 creates no `audit_events` table. Actor identity on
  lifecycle events stays a nullable, reference-free value in Phase 3.5 — no
  Worker or User foreign key. Machine retire/reactivate is a Management
  action, so any future authenticated actor linkage belongs to Users and
  authentication (Phase 14); Workers are Scan-Station-scoped production
  audit identity, separate from Users, and are never associated with
  Machine lifecycle events.
- the required active/inactive flags and the exact barcode ownership
  PROJECT_PROFILE §10 defines — no other barcode namespace is invented:
  Areas carry `PF:AREA:<stable-id>`; Machines carry
  `PF:MACHINE:<asset-tag>`, always derived from the immutable Asset Tag;
  Departments have no barcode; Operations get no barcode field; Scan
  Stations are identified by their stable Station ID and Area binding —
  there is no `PF:STATION` or any other station barcode. This includes the
  Administration → Barcode configuration Asset Tag format (prefix +
  zero-padded numeric sequence, PROJECT_PROFILE §8.6) that Machine creation
  requires in order to auto-assign Asset Tags

Explicitly NOT in this prerequisite: no production workflow and no
production write of any kind — no Work Order intake or production release,
no QuantityFlow creation, no PartMovement workflow or movement-type
widening, and no transfer or Machine-assignment workflows (Phases 4–6 own
those); not the generic `audit_events` table (Phase 4, SLICE1_DATA_MODEL
§16); not the real Planned Routes management and not the real Part Numbers
management (both Phase 13, through Management → Planned Routes and
Management → Part Numbers); and none of the remaining administration
capability, which stays in Phase 13 and Phase 14: Workers, Users and roles,
authorization management, Worker-session policies beyond the immediate
workflow requirements, correction permissions, retention/archive/purge
policies, general settings, and every other nonessential administration
capability. The Phase 2 Administration view remains a development-only
visual shell/reference until this prerequisite and later Phase 13 make its
sections real.

## Phase 4 — Manual Work Order Intake and Production Release

The first business vertical slice, presented in the UI as the Work Orders view (GUI_DESIGN §11). It must:

- create/find WorkOrder,
- save a confirmed blank external WO Number as `NULL` on an internal Work Order (rendered `—`, never persisted as a placeholder; replaceable later through an audited edit — PROJECT_PROFILE §7 Work Order; no temporary number is generated),
- accept null WorkOrder and WorkOrderDemand due dates as valid data (PROJECT_PROFILE §8.2/§8.3),
- create/find the PartNumber master for the canonical PN (uppercase, whitespace-free normalization; create-on-first-use),
- provide the minimal operational capability to view/print the derived `PF:PN:<canonical-part-number>` barcode label (PROJECT_PROFILE §10) for a PartNumber created/found during intake — Phase 5 production scanning requires physical PN barcodes. This is the only Part Numbers capability in this phase: it does not make the full Management → Part Numbers screen real (Phase 13),
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

## Phase 13 — Full Administration

Completes the Administration view beyond the Phase 3.5 minimum environment
setup (which already made Departments, Areas, Operations, Scan Stations,
Machines and their active/barcode fields configurable):

- Workers — profiles with stable id, name, badge barcode (the company's
  existing employee badge, exact-matched; no `PF:WORKER:` barcodes), avatar,
  and active status (PROJECT_PROFILE §8.13); kept separate from Users, never
  merged
- Route Templates — real management of the reusable route definitions through
  **Management → Planned Routes** (GUI_DESIGN §13), permission-based for
  authorized production roles; Administration keeps no duplicate Route
  Templates screen
- Part Numbers — real management of the optional PartNumber master metadata
  through **Management → Part Numbers** (GUI_DESIGN §14), permission-based
  for authorized production roles; Administration keeps no duplicate Part
  Numbers screen. Phase 4 already creates/finds PartNumber master records as
  part of Work Order Intake (create-on-first-use) and already offers the
  minimal derived `PF:PN:` label view/print that Phase 5 scanning depends
  on, but neither makes this management screen real: metadata editing,
  image management, hard deletion per PROJECT_PROFILE §28, and the full
  management screen (including its label surface) arrive only here
- Users and roles / authorization management (enforced in Phase 14)
- Worker session policies — the scanned-session sliding inactivity timeout:
  one default value with per-Area overrides (PROJECT_PROFILE §19)
- correction permissions
- Department display settings — per-Department Production Board rotation
  timing (seconds per displayed row, minimum page dwell — PROJECT_PROFILE §21)
- theme persistence per User and per Scan Station (GUI_DESIGN §2.1 — the
  Phase 2 mock keeps the theme session-only until this exists)
- scan-behavior policies, retention/archival policy settings (executed in
  Phase 16), and general settings

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
- **administrative archival/purge maintenance** (PROJECT_PROFILE §28): PN master metadata hard-deletion (never cascading into production data — history keeps displaying the canonical PN normally); Movement-history retention with a configurable retention period (e.g. N years in the primary database), executed as select-by-cutoff → lossless archive export → verify → purge exactly the archived rows, through a separate privileged Admin maintenance path (the application role keeps its UPDATE/DELETE revocation on `part_movements` at all times) and keeping related Movements (e.g. `reverses_movement_id` chains, atomic-command groups) whole so no retained Movement references a purged row, with retention policy / size threshold / manual triggers, scope preview, mandatory reason, and full audit — normal runtime stays append-only; no full retention/archival engine is built before this phase

## Deferred

- ERP synchronization
- offline scan synchronization (not part of MVP; production writes stay blocked while disconnected)
- WebSocket/SSE push connectivity (event-driven + polled health detection is the approved mechanism)
- advanced analytics
- speculative automation
- broad ERP/MES features
