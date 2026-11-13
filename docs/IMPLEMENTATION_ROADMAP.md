# PartFlow Implementation Roadmap

> **Authority:** Canonical for implementation order, phase boundaries, dependencies, the current implementation state
> and temporary limitations.
> **Language:** English is the source of truth. [Tiếng Việt](./IMPLEMENTATION_ROADMAP.vi.md).

This document says in which order PartFlow is built, what each phase contains, what exists in the code today, and
what is still missing or temporarily limited. It is written for the developer or operator who needs to know what the
system can do now and where the remaining work lies. It works together with the other canonical documents:
[`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md) owns the domain rules, invariants, workflows and product scope;
[`GUI_DESIGN.md`](./GUI_DESIGN.md) owns the approved user interface; this roadmap owns implementation order, phase
boundaries, dependencies, the current implementation state and temporary limitations; the slice documents, such as
[`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md), are subordinate implementation detail; and
[`DEPLOYMENT.md`](./DEPLOYMENT.md) owns deployment. Unlike the other canonical documents, this roadmap must match the
code: when the code changes, the affected phase is rewritten here.

## Current State

**What PartFlow can do today.** PartFlow tracks physical production quantity by Part Number (PN) through the factory.
Managers enter Work Orders by hand or import them from a comma-separated values (CSV) or Excel file, and release
quantity to production. Operators at Scan Stations receive new quantity, transfer it between Areas, assign it to
Machines, mark Area processing as done, split and combine quantities, send quantity back for repair, record scrap and
found quantity, and undo recent mistakes. The Stockroom receives finished quantity and allocates it to Work Order
demand, which completes Work Orders. The Production Board, the Area Board and PN Tracking show where every quantity
is, and the Hot list (Priority) ranks urgent demand. Administration configures Departments, Areas, Operations, Scan
Stations, Machines, Workers and their badge sessions, Users, roles and permissions, and the application policies.
Users sign in, every Management and Administration action is checked against permissions, and only enrolled devices
can act as Scan Stations. The complete production stack (images, Compose file, release script, backups, restore
drill, database roles, monitoring and reconciliation) runs on the company Synology NAS, where PartFlow went live on
2026-11-09 with release `v1.0.0`; later releases are installed from git tags with `release.sh`.

**Phases.**

| Phase | Status | Summary |
| --- | --- | --- |
| Phase 1 — Repository Foundation | Closed (2026-07-23) | Frontend, backend, PostgreSQL, Alembic, Docker Compose, health endpoint, tooling and continuous integration (CI). |
| Phase 2 — Frontend Design System and Application Shell | Closed (2026-08-13) | Design tokens, Dark/Light themes, routing, connectivity detection, view states and the mock-free production build boundary. |
| Phase 3 — Minimum Canonical Domain and Data Foundation | Closed (2026-08-18) | Domain vocabulary, canonical PN rule and the first twelve tables, with append-only Movements. |
| Phase 3.5 — Minimum Environment Setup | Closed (2026-08-19) | Departments, Areas, Operations, Scan Stations, Machines and the Asset Tag format, with their API and screens. |
| Phase 4 — Manual Work Order Intake and Production Release | Closed (2026-08-24) | Work Orders with demand lines, the PN label and the explicit, partial-capable release to production. |
| Phase 5 — Scan Station Transfer to an Area Queue | Closed (2026-08-25) | The real Scan Station and the transfer of quantity between Areas, with route deviation confirmation. |
| Phase 6 — One-Shot Machine Assignment and Area Completion | Closed (2026-08-26) | Machine assignment, the `QUEUE` return and the `DONE` Area completion in Areas with Machines. |
| Phase 7 — Direct Area Processing (Areas Without Machines) | Closed (2026-08-28) | Direct processing and the Machine-less `DONE` in Areas without Machines. |
| Phase 8 — Quantity SPLIT and MERGED Workflows | Closed (2026-08-31) | Partial quantity on every Scan Station command and the explicit `Combine quantities`. |
| Phase 9 — Undo, Corrections, and Auditable Quantity Events | Closed (2026-09-01) | Command-level Undo, Repair, Scrap and quantity additions. |
| Phase 10 — Stockroom and WorkOrderAllocation | Closed (2026-09-01) | Stockroom receiving, allocation to demand, derived Work Order completion and the completed history. |
| Phase 10.5 — Scan Station Receive Quantity Gap Closure | Closed (2026-09-04) | `Receive Quantity` at a production Scan Station for a PN with no active demand. |
| Phase 11 — Read Models and Monitoring Views | Closed (2026-09-14) | Production Board, Area Board, PN Tracking, the Machine per-PN breakdown and expected-duration monitoring. |
| Phase 12 — Priority Management | Closed (2026-10-04) | The audited Hot list with automatic removal of inactive entries. |
| Phase 13 — Full Administration and Production Identity Configuration | Closed (2026-10-07) | Workers, Worker identification and sessions, master-data management, policies, Users and roles as configuration. |
| Phase 14 — Authentication, Role Enforcement, and Authorized Management Corrections | Closed (2026-10-08) | Sign-in, permission enforcement, enrolled Scan Station devices and the authorized correction workflows. |
| Phase 15 — File-Based Work Order Import | Closed (2026-10-08) | Creating and updating Work Orders from CSV or Excel files. |
| Phase 16 — Deployment, Production Hardening, and Admin Maintenance | In progress | Production stack implemented and live on the company NAS since 2026-11-09; the host checks after go-live, the open follow-ups and Movement-history archival remain. |

**Database schema.** The current Alembic migration head is `0032_phase14_route_adjusted`
(`backend/alembic/versions/20261007_0032_phase14_route_adjusted.py`). Phases 15 and 16 added no migration.

**Owner decision of 2026-11-09: direct go-live.** PartFlow went into real use on the company Synology NAS on
2026-11-09, as soon as the production stack was installed. There is no pilot phase and no list of release gates;
problems are fixed in later releases while the system is in use. Only what the software needs in order to work is
required; backups, monitoring and the host checks are set up after go-live, when convenient, and block nothing
(DEPLOYMENT §1 and §5).

**Where the other documents are.**

- Domain rules: [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md).
- Approved user interface: [`GUI_DESIGN.md`](./GUI_DESIGN.md), with the visual reference
  [`mockups/partflow-gui-mockup-v18.html`](./mockups/partflow-gui-mockup-v18.html).
- First data-model slice: [`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md).
- Deployment: [`DEPLOYMENT.md`](./DEPLOYMENT.md), with the day-to-day procedures in
  [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) and the NAS guide in
  [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md).

## Implementation Principles

- Build in vertical slices; complete one workflow end to end before expanding.
- Never transfer quantity before the system has a real quantity-introduction workflow: Work Order Intake and production release precede all real transfer workflows.
- Preserve Presentation → Application → Domain → Infrastructure.
- Use mock data only until the relevant backend slice exists; mock behavior never leaks into production.
- Ambiguity always requires explicit confirmation before any write; unknown or invalid input is rejected with no write.
- Production writes are blocked while disconnected; offline scan synchronization is deferred and unapproved.
- Do not implement enterprise resource planning (ERP) integration during the minimum viable product (MVP).
- Every behavior described as `deliberately absent`, `temporary`, `placeholder`, `later`, or otherwise deferred must name exactly one owning later phase, or be listed explicitly under `Deferred` / a canonical open decision. A phase must never close with an unowned target behavior.
- Before closing any phase, audit `PROJECT_PROFILE.md` + `GUI_DESIGN.md` + the current production code against this roadmap. Close the phase only when every in-scope target behavior is implemented and every intentional omission has an explicit later owner.

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

**Delivered.** The React + TypeScript frontend, the FastAPI backend, PostgreSQL, Alembic, the development Docker
Compose stack (`compose.yaml`), the `/api/health` endpoint, the formatter, linter, type-check and test tooling, and the
CI workflow.

**Temporary limitations.** None.

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

**Delivered.**

- Semantic design tokens with switchable Dark and Light themes (Dark by default). Where the theme choice is saved is
  described under Phase 13 (Scan Station tier) and Phase 14 (User tier).
- URL-based routing and navigation; Management remembers its last-used sub view for the session.
- Fast connectivity detection (GUI_DESIGN §3.6): browser online and offline events, health polling about every second
  with a request timeout below the polling interval and no overlapping probes, a recheck when the tab regains focus or
  visibility, passive probes that never switch the interface to a "connecting" state, the persistent OFFLINE banner
  with Retry, write controls disabled while disconnected, and the Scan Station input re-enabled and refocused on
  recovery. There is no WebSocket or server-sent events (SSE) push and no offline write queue.
- Loading, empty, error, disconnected and long-data states, with the development-only `?state=` preview.
- The production build boundary: mock views and datasets load only when `import.meta.env.DEV` is true, `src/mocks/`
  never enters a production bundle, and `npm run build` fails when a mock sentinel is found in the production assets
  (`src/production-boundary.test.ts` also guards the production module graph). All ten approved views were first built
  as mock views here and later became real views on the server API (Phases 3.5 to 13); `src/app/real-views.ts` now
  lists all ten, and no development-only mock view is registered.
- The automated rendered-copy guard (`src/rendered-copy.test.ts`) and the vocabulary migration from Purchase Order to
  Work Order across documentation, code, routes and tests.

**Temporary limitations.** The development-only mock Scan Station preview (`ScanStationMockView.tsx`, reached through
`?preview=mock` in development builds) and its mock datasets still exist; they never enter a production build. Their
removal is listed under `Deferred`.

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

**Delivered.**

- The framework-independent domain vocabulary in `backend/app/domain/`: the canonical PN rule (trim surrounding
  whitespace, reject internal whitespace, convert to UPPERCASE; `InvalidPartNumberError`) and the enumerations, each
  widened additively by the phase that introduces new values.
- The SQLAlchemy mappings in `backend/app/infrastructure/models.py` and the migration `0002_phase3_domain`, which
  creates twelve tables with explicit constraint and index names: `departments`, `areas`, `operations`, the optional
  `part_numbers` master (the canonical PN string as natural primary key, no surrogate id, and no foreign key from any
  production table, so the master stays hard-deletable — PROJECT_PROFILE §8.1 and §28), `work_orders` (nullable
  external number, unique among non-null numbers only), `work_order_demands` (Job Numbers are a plain text array —
  metadata, never an aggregate), `route_templates` and `route_steps` (no version column; snapshots preserve history),
  the independent `assigned_routes` and `assigned_route_steps` snapshot tables, `quantity_flows` (route mode,
  `assigned_route_id` present exactly for `PLANNED`, the `current_area_id` current-position column, and the
  `(id, part_number)` composite target), and the append-only `part_movements` table (positive quantity, unique
  idempotency key, a composite foreign key so a Movement can never carry another flow's PN, and a statement-level
  trigger that rejects UPDATE, DELETE and TRUNCATE in PostgreSQL itself).
- On the production stack the application database role holds only SELECT and INSERT on `part_movements` (Phase 16
  database roles); the development and test databases keep one owner role, where the trigger alone binds every path
  that does not disable it.

**Temporary limitations.** None. The generic `audit_events` table belongs to Phase 4, and the environment tables to
Phase 3.5.

## Phase 3.5 — Minimum Environment Setup (named prerequisite — no later phases renumbered)

The smallest administration capability required to operate the initial production slices, deliberately separated
from the later full Administration phase (Phase 13). It sits after the domain foundation (Phase 3) and **before any
real production workflow needs a configured environment**: Phase 4 needs configured Departments, Areas and Operations
for release, and Phase 5 needs active, Area-bound Scan Stations.

Scope — configuration management for exactly:

- Departments
- Areas (identity, color/display properties, terminal flag)
- Operations (per Area)
- Scan Stations (Station ID, bound Area, active status)
- Machines (per Area, lifecycle and maintenance status) — configured through **Management → Machines**
  (GUI_DESIGN §12), permission-based for authorized production roles rather than an Administration panel, but still
  part of this minimum setup prerequisite: Machines must be configured before real production runs
- Machine lifecycle event history — the dedicated append-only **`machine_lifecycle_events`** table (canonical name)
  persisting the `RETIRED` and `REACTIVATED` lifecycle events PROJECT_PROFILE §8.6 requires, created in this phase
  together with `machines`. A retirement or reactivation and its lifecycle event commit atomically — one transaction,
  no lifecycle change without its event and no event without its change — and recorded events are
  immutable/append-only. Each event records the canonical lifecycle context: event type, Machine identity, occurred
  time, reason, before/after state, and the previous and current Area when the Machine moved while retired. This is
  Machine lifecycle/history persistence, deliberately **not** a generic audit framework and **not** the Slice 1
  `audit_events` mechanism (SLICE1_DATA_MODEL §16); Phase 3.5 creates no `audit_events` table. Machine
  **configuration** writes — creation, metadata and maintenance-context edits, maintenance start/clear, and the
  configuration delta a retirement draft or a reactivation carries — are audited in `audit_events` (Phase 13), while
  retirement and reactivation themselves stay recorded only as these lifecycle events. Machine retire/reactivate is a
  Management action, so the actor of a lifecycle event is the signed-in User (Phase 14); Workers are
  Scan-Station-scoped production audit identity, separate from Users, and are never associated with Machine lifecycle
  events.
- the required active/inactive flags and the exact barcode ownership PROJECT_PROFILE §10 defines — no other barcode
  namespace is invented: Areas carry `PF:AREA:<stable-id>`; Machines carry `PF:MACHINE:<asset-tag>`, always derived
  from the immutable Asset Tag; Departments have no barcode; Operations get no barcode field; Scan Stations are
  identified by their stable Station ID and Area binding — there is no `PF:STATION` or any other station barcode. This
  includes the Administration → Barcode configuration Asset Tag format (prefix + zero-padded numeric sequence,
  PROJECT_PROFILE §8.6) that Machine creation requires in order to auto-assign Asset Tags

Explicitly not in this prerequisite: any production workflow or production write (Phases 4 to 6); the generic
`audit_events` table (Phase 4, SLICE1_DATA_MODEL §16); the real Planned Routes and Part Numbers management (Phase 13,
through Management → Planned Routes and Management → Part Numbers); and the remaining administration capability —
Workers, Users and roles, authorization management, Worker-session policies, correction permissions and the
Movement-history retention period setting (Phase 13, with enforcement in Phase 14 and archival and purge execution in
Phase 16). General settings and scan-behavior policies are listed under `Deferred`.

**Delivered.**

- Persistence (migration `0003_phase35_environment`): `areas` gains description, color, the terminal flag and the
  server-assigned stable `PF:AREA:<id>` barcode (an assign-once trigger guards it); `operations` gains description, an
  optional expected duration and the external flag; new tables `scan_stations` (stable URL-safe Station ID as natural
  key, Area binding, active flag, no station barcode), `machines` (immutable auto-assigned Asset Tags guarded by
  triggers, display names unique among the active Machines of one Area, the explicit maintenance override,
  `state_changed_at`, retirement dates), the append-only `machine_lifecycle_events`, and the singleton Asset Tag format
  with its never-reused counter.
- API: `/api/departments`, `/api/areas`, `/api/operations`, `/api/scan-stations` and
  `GET`/`PUT /api/barcode-configuration/machine-asset-tag-format` (create, read, update and deactivate only — no
  DELETE endpoints; server-owned fields are refused), and `/api/machines` (listing, creation with server-side Asset Tag
  allocation that takes the previewed tag only as an optimistic precondition, one Save-changes transaction for metadata
  and the maintenance context, maintenance start and clear, and retirement and reactivation each committing atomically
  with its lifecycle event). Application services own every rule and transaction; typed errors map centrally to HTTP
  responses.
- Frontend: Administration → Departments, Areas, Operations, Scan Stations and Barcode configuration, and Management →
  Machines are real views with loading, error and retry states, server refusals shown in place, and every write
  blocked while offline. The real views ship from `src/app/real-views.ts` with the shared API client in `src/api/`.

**Temporary limitations.** None.

## Phase 4 — Manual Work Order Intake and Production Release

The first business vertical slice, presented in the UI as the Work Orders view (GUI_DESIGN §11). It must:

- create/find WorkOrder,
- save a confirmed blank external WO Number as `NULL` on an internal Work Order (rendered `—`, never persisted as a placeholder; replaceable later through an audited edit — PROJECT_PROFILE §7 Work Order; no temporary number is generated),
- accept null WorkOrder and WorkOrderDemand due dates as valid data (PROJECT_PROFILE §8.2/§8.3),
- create/find the PartNumber master for the canonical PN (uppercase, whitespace-free normalization; create-on-first-use),
- provide the minimal operational capability to view/print the derived `PF:PN:<canonical-part-number>` barcode label (PROJECT_PROFILE §10) for a PartNumber created/found during intake — Phase 5 production scanning requires physical PN barcodes. This is the only Part Numbers capability in this phase: it does not make the full Management → Part Numbers screen real (Phase 13). In the UI the label was first reached by activating the demand-line PN itself (GUI_DESIGN §11.2); since Phase 13 that control opens the shared `Edit Part Number` dialog, from which the label stays reachable,
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

**Delivered.**

- **Audit table.** Migration `0004_phase4_audit` adds the generic append-only `audit_events` table (SLICE1_DATA_MODEL
  §16 and §17) with a raise-on-write trigger. Every Work Order, demand line and PartNumber master creation or edit
  commits with its audit row. Production release writes no audit row: the immutable `RECEIVED` Movement is the
  production audit record.
- **Work Orders API** (`/api/work-orders`): create and find (a confirmed blank number is saved as `NULL` on an internal
  Work Order), list and search (the active list is bounded at 100 rows; the exact `number=` lookup never is, so a
  duplicate number can never hide behind the bound), a one-transaction PATCH for the audited header edit plus diff-only
  line edits and new lines (canonical PN, master created on first use), and DELETE of a demand line under the removal
  rule (the last remaining line is never removable). Part Number lookup on `/api/part-numbers` returns at most 50
  masters per listing with the derived `PF:PN:` barcode; the exact `number=` resolution is never bounded away.
- **Release command** `POST /api/work-orders/{work_order_id}/demands/{demand_id}/release`. It validates the canonical
  PN, the starting Area (active **and non-terminal**, enforced in the Application layer), the Operation, an active
  Route Template for `PLANNED` (from the active-only `GET /api/route-templates`), and a positive quantity within the
  demand's remaining quantity. When the PN already has active quantity it requires the explicit confirmation flag and
  shows the existing distribution. It snapshots an AssignedRoute for `PLANNED` only, creates the QuantityFlow, appends
  the `RECEIVED` Movement with the demand context as metadata, and commits atomically. `device_event_id` idempotency
  stores a request fingerprint: the same id and fingerprint replays, a mismatched reuse is a conflict, and neither
  creates anything.
- **Partial release and the restricted edit.** Released quantity is derived from the `RECEIVED` metadata (no stored
  counter; a partial expression index from migration `0005_phase4_release_index` keeps the read fast). A Work Order
  reads `RELEASED` only when every current line is fully released. A demand may be released in several parts, each its
  own QuantityFlow, with the remaining quantity as a hard cap. A line with released quantity keeps only the restricted
  edit of PROJECT_PROFILE §13, enforced in the backend: requested quantity (never below the larger of the released and
  allocated quantities), due date and Job Numbers; a change to Request Type, requester, reason or notes is refused with
  a 409 that writes nothing at all, and removal stays refused. Each edited line is locked and re-read, so an edit and a
  release of the same line serialize; a save that adds lines takes the Work Order row lock, so one canonical PN appears
  at most once per Work Order.
- **Frontend.** Management → Work Orders (GUI_DESIGN §11): list and search, New Work Order resolving an existing number
  instead of duplicating it, Work Order Details with a diff-only save, the shared `Unsaved changes` decision before a
  release, the release dialog (GUI_DESIGN §11.4) with retry-safe `device_event_id` reuse and the active-quantity
  confirmation, restricted released lines, the audited external-number edit on internal Work Orders, and the printable
  `PF:PN:` Code 128 label.

**Temporary limitations.** None. Completion of a Work Order is derived from allocation (Phase 10).

## Phase 5 — Scan Station Transfer to an Area Queue

Worked example: `Material -> Lathe queue`, where Lathe is configured as `Queue -> select Machine by scan`.

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

**Delivered.**

- Persistence (migration `0006_phase5_transfer`): the movement type `TRANSFERRED`, the nullable
  `part_movements.station_id` (NULL on the Management release), and a per-type shape check.
- Read models: `GET /api/scan-stations/{station_id}/context` (an unknown station is 404, an inactive station or Area
  409 — never a silent substitute); `POST /api/scan-stations/{station_id}/scans/resolve` (a `PF:PN:` barcode or manual
  entry, canonicalized by the one domain rule, resolved into the PN's quantity in the station's Area and the explicit
  **transfer candidates** elsewhere, each judged `FLOATING`, `ON_ROUTE` or `DEVIATION`; several candidates are
  returned unranked and the operator must select exactly one); and `GET /api/areas/{area_id}/inventory`.
- The transfer command `POST /api/scan-stations/{station_id}/transfers` moves a chosen QuantityFlow into the station's
  Area. The source and destination the operator confirmed are preconditions: the station row is locked and must still
  be active and bound to exactly the confirmed Area, so the screen can never confirm Area A while the backend records
  Area B, and the target Area is judged (active, not terminal) on its locked re-read. The Operation resolves from the
  matched route step, else the single active Operation, else the operator's explicit choice. The command appends the
  immutable `TRANSFERRED` Movement and updates `quantity_flows.current_area_id` in the same transaction; idempotency
  answers 201 fresh, 200 on a replay (checked before any station, Area or Operation state is read) and 409 on a
  mismatched reuse. Lock order: flow → station → target Area → Operation.
- **Route deviation** (PROJECT_PROFILE §17) on a `PLANNED` flow — a different Area, or a different Operation at the
  expected Area — is refused until the request carries `confirm_route_deviation` with a mandatory
  `route_deviation_reason`; the confirmed deviation is recorded on the `TRANSFERRED` Movement itself, no step is
  recorded as matched, and the last known route position is preserved. No separate deviation Movement exists; route
  editing is the authorized AssignedRoute adjustment of Phase 14 (`ROUTE_ADJUSTED`, an audit event, never a Movement).
- Frontend: the Station Selector and the Scan Station are real views (GUI_DESIGN §4). The transfer wizard
  (GUI_DESIGN §4.7) requires an explicit source selection when several sources exist, shows the confirmation summary,
  reuses one `device_event_id` per intent on retry, reports success only after the server confirmed, then refreshes the
  inventory, updates Last Action and restores keyboard focus.

**Temporary limitations.** None today. The whole-QuantityFlow limitation this phase shipped with was lifted by Phase 8
(partial quantity), and transfers from actively processing quantity append `AREA_COMPLETED` + `TRANSFERRED` as one
atomic command since Phases 6 and 7 (PROJECT_PROFILE §8.11).

## Phase 6 — One-Shot Machine Assignment and Area Completion

- Machine barcode resolution (one-shot shortcut only — **no Machine session of any kind**)
- Machine/Area validation
- Machine-first (Machine scan opens the one-shot assignment dialog with the Machine preselected) and PN-first (assign action on queued quantity) entry points
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE` (the `QUEUE` return action — returns unfinished or paused quantity to the queue, never completed work)
- **`AREA_COMPLETED` for Machine Areas** (PROJECT_PROFILE §7 Area Completion, §8.11, §12): the manual DONE action on Machine-assigned quantity (`DONE` and `QUEUE` stay two distinct actions), deriving `READY_TO_TRANSFER`, clearing `current_machine_id`, keeping the Area as location; a transfer from `ON_MACHINE` quantity appends `AREA_COMPLETED` + `TRANSFERRED` in one transaction; DONE is quantity-scoped and never a PN status, never `STOCKED`
- inactive Machine rejection
- one Machine may hold quantities of multiple PNs; one Machine behaves exactly like several (never auto-assign by Machine count)

**Delivered.**

- Persistence (migration `0007_phase6_machine_assignment`): `quantity_flows.current_machine_id`, the Movement Machine
  references `source_machine_id` and `destination_machine_id`, and `part_movements.command_sequence`. One
  `device_event_id` identifies one **application command**, which may append several Movements
  (`UNIQUE (device_event_id, command_sequence)`), so Undo can reverse a command as a whole. The movement types admit
  `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE` and `AREA_COMPLETED`; the in-Area types stay in one Area at a Scan
  Station and carry exactly their Machine reference.
- The holding state is derived from the flow's latest Movement (`ASSIGNED_TO_MACHINE` → `ON_MACHINE`,
  `AREA_COMPLETED` → `READY_TO_TRANSFER`, otherwise `QUEUED`); the projection replay rebuilds Area, Machine and state
  from history.
- Commands (`app/application/machine_processing.py`): `POST /api/scan-stations/{id}/machine-assignments` (assign
  queued quantity; the Machine is locked and must be active, in the station's Area and not under maintenance; several
  PNs may share one Machine; nothing is ever assigned automatically), `machine-releases` (the `QUEUE` return to the
  queue) and `area-completions` (`DONE`: clears the Machine, keeps the Area, derives `READY_TO_TRANSFER`; allowed under
  a maintenance override). Each answers 201 fresh, 200 on replay and 409 on a mismatched reuse, also across command
  kinds. A transfer of `ON_MACHINE` quantity appends `AREA_COMPLETED` then `TRANSFERRED` as one atomic command.
  Lock order: flow → station → Machine → target Area → Operation.
- The Machine operational state stays derived (maintenance override, else Running while quantity is assigned, else
  Idle) and `state_changed_at` moves only on a real change; retirement is refused while quantity is assigned.
- Read models: `POST /api/scan-stations/{id}/machine-scans/resolve` (the one-shot Machine-first context; nothing is
  stored, and the command re-validates the Machine under its lock), `available_actions` and `requires_selection` per
  flow, and the inventory split into `queued`, `machines` (one card per active Machine with only its `ON_MACHINE`
  quantity) and `finished`.
- Frontend: the Scan Station renders the split, turns a Machine barcode into `Assign to Machine` with the Machine
  preselected, offers PN-first `Assign to Machine` and `Complete Area processing on {Machine}`, carries the distinct
  Machine-card `DONE` and `QUEUE` actions through quantity, summary and final confirmation, and announces the implicit
  completion on transfer. Management → Machines shows the server-derived state and assigned total.

**Temporary limitations.** None. Worker identity and the badge gates (Phase 13), Undo, Repair and Scrap (Phase 9) and
the Stockroom (Phase 10) arrived in their own phases.

## Phase 7 — Direct Area Processing (Areas Without Machines)

- Area without Machines takes direct processing ownership (no queue)
- Operation recorded and Machine null
- multiple-Operation confirmation
- **`AREA_COMPLETED` for direct processing**: the same manual DONE workflow without a Machine field, and implicit completion when directly processing quantity transfers to another Area (one atomic `AREA_COMPLETED` + `TRANSFERRED` command)
- exactly two Area modes exist: no Machines → direct processing; Machines → `QUEUE_AND_ASSIGN` (PROJECT_PROFILE §12) — no per-Area assignment-mode configuration and no single-Machine auto-assignment

**Delivered.**

- Persistence (migration `0008_phase7_direct_processing`): only the `AREA_COMPLETED` shape widens, so a completion
  may carry no source Machine (never a destination Machine). No column, table or Area configuration is added.
- **The Area mode is derived, never configured** (`app.application.machines.area_has_machines`): at least one active,
  non-retired Machine means `QUEUE_AND_ASSIGN`, otherwise direct processing. Adding the first Machine or retiring the
  last one moves the Area and its quantity between the modes without touching history.
- In an Area without Machines an arriving `RECEIVED` or `TRANSFERRED` derives `PROCESSING`: no queue, Machine NULL,
  the Operation recorded on the arrival (several active Operations need the explicit choice; nothing is picked).
- The Machine-less `DONE` (`app/application/direct_processing.py`, `POST /api/scan-stations/{id}/area-completions`
  without `machine_id`) appends exactly one `AREA_COMPLETED` with no Machine and derives `READY_TO_TRANSFER`; it is
  refused with zero writes in a Machine Area, as the Machine-Area `DONE`, `QUEUE` and assignment are refused on
  directly processing quantity. A transfer of `PROCESSING` quantity appends `AREA_COMPLETED` + `TRANSFERRED` as one
  atomic command; finished quantity transfers with `TRANSFERRED` alone. A direct `DONE` and a Machine `DONE` are
  distinct intents under the idempotency rule.
- Read models: `has_machines` on the station context and the inventory, `processing_state` with `PROCESSING`, and the
  inventory split `processing` / `finished`. Every flow carries the Operation recorded on its latest Movement, whatever
  that Operation's current activation, so existing quantity keeps its recorded Operation after a deactivation.
- Frontend: the Scan Station takes the Area mode from the freshest server read, shows directly processing quantity as
  `In processing` (`External processing` for an external Operation) and `Finished — ready to move`, offers the single
  `DONE` row action and `Complete Area processing`, reuses the Area Completion wizard without a Machine field, and
  announces the implicit completion on transfer (`Recorded events: AREA_COMPLETED, then TRANSFERRED (one command)`).

**Temporary limitations.** None.

## Phase 8 — Quantity SPLIT and MERGED Workflows

- partial movement
- preserved lineage
- quantity conservation
- `SPLIT` and `MERGED` Movement history
- quantity keypad supports valid partial amounts
- **SPLIT is the prerequisite for partial Repair** (Phase 9): before SPLIT exists, partial Repair is refused clearly with no write — it must not be claimed as supported

**Delivered.**

- Persistence (migration `0009_phase8_split_merge`): the movement types `SPLIT` and `MERGED` (one Area at a Scan
  Station, no Machine — a lineage event never restates a position), the Quantity Flow lifecycle (`status` `ACTIVE`,
  `SPLIT` or `MERGED`, with `closed_at` set exactly for a closed flow), and the append-only `quantity_flow_lineage`
  edge table (parent → child, relation, the command's `device_event_id`). One edge per child records a split (1 → N)
  and one per consumed source records a merge (N → 1), which is why no single `quantity_flows.parent_flow_id` column
  exists (SLICE1_DATA_MODEL §9 and §18).
- Derivation (`app/application/projections.py`): a child or merge result that has not moved inherits its holding
  state, Machine and Operation by following its lineage to the last position-bearing Movement
  (`projections.effective_latest_movement`); consumed flows are exactly the parents in the graph and never active
  inventory.
- Partial quantity on every command: Assign, `QUEUE`, Machine `DONE`, direct `DONE` and Transfer accept a quantity
  smaller than the flow's. The source splits atomically inside the same command (source, selected child and remainder
  `SPLIT` rows, then the action rows, all under one `device_event_id`); a `PLANNED` source gives each child its own
  snapshot copy at the source's route position; the action applies to the selected child only, the remainder keeps the
  source's state, and the whole quantity never splits. `WorkOrderDemand` is never touched. Two commands on one source
  serialize on the source row lock, and the loser is refused ("was split into separate quantities").
- Merge (`app/application/merges.py`, `POST /api/scan-stations/{id}/merges`): an explicit command naming at least two
  `ACTIVE` flows of one PN in the station's Area, accepted only when their production context is identical (holding
  state, Machine, recorded Operation, and route context — all `FLOATING`, or all `PLANNED` with structurally equal
  snapshots at the same step); anything else is refused with zero writes, and nothing is ever merged automatically.
  The PN resolution reports `combine_groups`, computed by the same rule the command enforces, so the frontend holds no
  compatibility rule of its own.
- Frontend (GUI_DESIGN §4.7 and §4.8): every wizard accepts 1 to the available quantity, shows the chosen part and the
  remainder before and after the write and submits only the chosen quantity; the PN action dialog offers
  `Combine quantities` for exactly the server-reported groups, with source selection, a result preview and the
  `Confirm combine` summary.

**Temporary limitations.** None.

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

**Delivered.**

- Persistence (migration `0010_phase9_undo_corrections`): the movement types `SCRAPPED`, `QUANTITY_ADJUSTED` and
  `REVERSED` with their shapes; `part_movements.movement_reason` (only `REPAIR`, only on `TRANSFERRED`); the free-text
  `reason` (mandatory for Scrap, quantity additions and every Repair, enforced by a CHECK); `reverses_movement_id` (set
  exactly on a `REVERSED`, UNIQUE, so a Movement can never be reversed twice, even by a race); and the flow statuses
  `SCRAPPED` and `REVERSED`.
- Derivation: every derivation excludes the reversed pair (the `REVERSED` rows and the originals they reference), so
  after an Undo the position, Machine, Operation, route position and holding state are exactly those before the undone
  command, also through lineage.
- Undo (`app/application/undo.py`; `GET /api/scan-stations/{id}/undo-preview/{device_event_id}` and
  `POST /api/scan-stations/{id}/undos`) reverses the complete committed command by appending one compensating
  `REVERSED` row per original, in reverse order, under the Undo's own `device_event_id`. Eligible is only the most
  recent effective operation of every flow involved, recorded at this station, not recorded by Management, not already
  reversed and not itself an Undo; restoring onto a retired Machine or into a deactivated Area is refused. Eligibility
  is judged under the row locks with zero writes on refusal. The preview serves the summary confirmation with the
  eligibility verdict. A Production release, a Stockroom stocking (Phase 10), a Scan Station receipt (Phase 10.5) and
  a command that created a flow whose route was later adjusted (Phase 14) are never Undo targets. The reason
  requirement and authorization arrived with Phases 13 and 14 (see there).
- Repair is the explicit `repair` intent with a mandatory reason on the same transfer command
  (`TRANSFERRED · movement_reason REPAIR`): no Repair record, Request Type or demand is created; the destination must
  be an Area the quantity visited before; partial Repair rides the in-command split; a route deviation is still
  confirmed separately. The PN resolution marks eligible candidates `repair_available`.
- Scrap (`app/application/quantity_events.py`, `POST /api/scan-stations/{id}/scraps`) appends one `SCRAPPED` per
  confirmation with its mandatory reason and closes the flow or the split-off part; the `PF:SCRAP` counting happens
  in the client and writes nothing. Scrapped totals are net of reversed scraps.
- Quantity addition (`POST /api/scan-stations/{id}/quantity-additions`) introduces found quantity as a new `FLOATING`
  flow whose first Movement is `QUANTITY_ADJUSTED` with direction `INCREASE` and the mandatory reason, only beside
  quantity of the PN already active in the station's Area, never touching a requested quantity.
- Frontend: the Scan Station offers `Add more quantity`, `Return quantity for repair` (only for `repair_available`
  sources, also through an explicit Transfer-or-Repair choice), the Scrap workflow (GUI_DESIGN §4.9; `PF:SCRAP` counts
  only inside it), and Undo from the Last Scanned PN block, which walks the session's completed commands newest first
  through the server's preview to find the most recent eligible operation, shows the structured summary and a
  warning-toned final question, and advances to the next eligible operation after a confirmed reversal.

**Temporary limitations.** None.

## Phase 10 — Stockroom and WorkOrderAllocation

- `STOCKED` Movement
- available stocked quantity
- suggested allocation follows the canonical demand ordering (PROJECT_PROFILE §18):
  1. highest manager-defined Work Order Demand priority
  2. within the same priority: dated demand earliest-first; undated demand after all dated demand, ordered by parent WorkOrder received_date (oldest first)
- equal values resolved by a stable deterministic tie-breaker (implementation detail, not a business rule)
- WorkOrderAllocation recorded separately from PartMovement
- the allocation adjustment capability (the auditable reversal) — its role-based authorization is enforced by Phase 14, never earlier

**Delivered.**

- Persistence (migration `0011_phase10_stock_allocation`): the movement type and flow status `STOCKED`,
  `work_orders.completed_at`, and the append-only `work_order_allocations` table (allocation and reversal rows,
  canonical PN by value, the demand foreign key, source `STOCKROOM` or `MANAGEMENT`, the manual-override flag,
  `reverses_allocation_id` UNIQUE so one reversal per allocation even under a race, and a raise-on-write trigger). An
  allocation never references a Movement or a QuantityFlow.
- Stocking (`POST /api/scan-stations/{id}/stockings`) is the transfer's own arrival mechanism at a station bound to a
  terminal Area, with the same source selection, locks, route assessment, in-command split and implicit
  `AREA_COMPLETED`. The flow closes as manufacturing-complete (`status = STOCKED`), leaves every read model and the
  projection replay, and is never undone; allocation adjustment is the correction path. A transfer into a terminal
  Area stays refused.
- Allocation (`app/application/allocations.py`): available stocked quantity is derived (effective `STOCKED` minus
  active allocation rows), never stored; `work_order_demands.allocated_quantity` and `work_orders.completed_at` are
  projections rebuildable from the allocation rows. `GET /api/allocations/suggestion` proposes in the canonical demand
  ordering (Hot rank, dated lines earliest first, undated lines by the Work Order's received date, the demand id as
  tie-breaker) up to each line's remaining shortage and never beyond the available quantity. The Stockroom confirmation
  (`POST /api/allocations`) names the explicit `allocation_quantity` the lines must sum to, which the available stocked
  quantity must still cover under the lock. A line never exceeds its shortage and the allocation never exceeds the
  available quantity; both are judged under a per-PN advisory lock plus the demand and Work Order row locks. The
  reversal (`POST /api/allocations/{id}/reversals`) is the auditable adjustment: once per allocation, mandatory
  reason, returning the quantity to stock and reopening a completed Work Order.
- Completion: a Work Order whose every demand line is fully allocated is completed (by an allocation, or by a demand
  save or line removal that leaves every remaining line fully allocated), leaves the active list and becomes read-only
  history; edits, line removal and release are refused, and the exact `number=` lookup still finds it, so its number is
  never duplicated.
- Completed history: `GET /api/work-orders/completed` with search, the Done range (named presets resolved on the
  site's current date, or explicit dates), the due outcome, server-side sorting (Done descending by default), keyset
  paging and the filter-independent `history_total`. Every date judgement uses the site calendar (`SITE_TIMEZONE`, an
  IANA time zone, `UTC` by default).
- Frontend: a station bound to a terminal Area opens `Receive into Stockroom`; after the server confirmed the stocking,
  the allocation dialog (GUI_DESIGN §10) shows the suggestion for exactly the stocked quantity and enables
  `Confirm allocation` only when the allocated total equals it; `Leave in stock — allocate later` records nothing. The
  Completed Work Orders page (GUI_DESIGN §11.5) is a real view.

**Temporary limitations.** None.

**Open follow-ups.** Whether stocked quantity may be returned to active production through a controlled reversal is
an open owner decision (PROJECT_PROFILE §32); until it is decided, stocked quantity never returns to production.

## Phase 10.5 — Scan Station Receive Quantity Gap Closure (named corrective prerequisite — no later phases renumbered)

This corrective phase closes a workflow gap found after Phase 10: Phase 5 intentionally deferred Scan Station `Receive Quantity`, and Phase 9 still distinguishes it from `Add more quantity`, but no later phase owned its implementation. It had to be completed before the remaining Phase 11 work was considered complete and before Phase 12 started.

Scope:

- implement the real Scan Station **`Receive Quantity`** workflow of GUI_DESIGN §4.7 / PROJECT_PROFILE §14 when a canonical PN has no active Work Order Demand, including a PN seen for the first time;
- manual PN entry and `PF:PN:<part-number>` scanning resolve through the same canonical PN rule: trim surrounding whitespace, reject internal whitespace, canonicalize to uppercase, and create the optional PartNumber master on first valid use;
- keep the approved three-step workflow: settings → quantity → confirmation, with editable defaults `Request Type = MODIFY` and `Route Mode = FLOATING`, optional due date, Planned Route selection only for `PLANNED`, the station Area / resolved Operation context, reason/notes, and the canonical internal Work Order behavior; `Confirm receipt` is the only write point;
- create or reuse the internal WorkOrder / WorkOrderDemand exactly as PROJECT_PROFILE §14 defines. Several plausible blank-number MODIFY Work Orders require explicit selection — never a guessed first match;
- a confirmed receipt creates the production quantity transactionally: the required business-demand records, QuantityFlow, an independent AssignedRoute snapshot only for `PLANNED`, the immutable `RECEIVED` Movement, and the current-position projection commit atomically or not at all;
- a scan-driven `RECEIVED` records the Scan Station identity and resolved Operation. The station must still be active and bound to the confirmed active, non-terminal Area when the command commits; stale station / Area / Operation context is refused with zero writes;
- use the existing one-shot command/idempotency model: one `device_event_id` per frozen intent, deterministic fingerprint, same-intent replay, conflicting reuse rejected, lost-response retry under the same id, and success displayed only after server confirmation;
- refresh station context/inventory and restore scan focus after success; invalid, ambiguous, cancelled, offline, stale, or rejected input produces zero writes;
- keep **`Add more quantity`** separate: it remains the Phase 9 `QUANTITY_ADJUSTED · INCREASE` correction beside quantity already active in the station Area and must never substitute for initial `Receive Quantity`;
- preserve the production build boundary: the real workflow uses server state and never imports or depends on the development-only Scan Station mock;
- record `received_date` from the SCAN, not the confirmation (PROJECT_PROFILE §14): the resolution issues the instant, the wizard carries it, the server validates it and derives the date on the site calendar;
- serialize the entry condition against every command that can give the PN active demand or active quantity, so a receipt can never commit beside one of them;
- receive a PN that ALREADY has active quantity only after the explicit confirmation PROJECT_PROFILE §14 requires, and record it as a SEPARATE QuantityFlow: a receipt never joins, merges, mutates or rewrites existing quantity, the resolution carries the existing distribution the operator confirms against, the backend re-judges that state authoritatively at write time (a receipt whose PN gained active quantity after the wizard opened is refused with zero writes until it is confirmed), and quantities that belong together are brought together afterwards by the existing `Combine quantities` workflow.

Not in this corrective phase: Worker identity, Worker Sessions and the badge gates (Phase 13); authentication and
role enforcement (Phase 14 — a receipt needs an enrolled station device and the Receive Quantity permission of the
role applied at Scan Stations); Planned Route and PartNumber metadata management (Phase 13); Stockroom allocation
(Phase 10).

**Delivered.**

- No migration: the `RECEIVED` shape already admits a Scan Station and a reason.
- Entry condition (`app/application/intake.py`): a Work Order Demand is active while
  `requested_quantity > allocated_quantity` (PROJECT_PROFILE §14). The PN resolution reports `has_active_demand`,
  `intake_available` (no active demand and a station Area that can start production — a terminal Area never can),
  `part_number_known`, `internal_work_orders` and the PN's existing `active_quantity`.
- The command `POST /api/scan-stations/{station_id}/receipts` creates in one transaction the PartNumber master on first
  use, the internal Work Order (no number, received date from the scan), the demand line, the `PLANNED` snapshot (its
  first step must start in the station's Area), the QuantityFlow and the `RECEIVED` Movement. Internal Work Order reuse
  never guesses: candidates are uncompleted Work Orders without a number that already carry a `MODIFY` line for the
  PN; exactly one is reused by raising that line (with its audit row), several are refused with `selection_required`
  and the candidates, none creates a new Work Order, and a `NEW` receipt never reuses.
- Never a join: where the PN already has active quantity, the command re-reads the distribution under the PN advisory
  lock and refuses (409, nothing written) until the operator confirmed; the confirmation flag is outside the request
  fingerprint, so confirming continues the same submission under the same `device_event_id`.
- Received date: the server validates the scan instant (time-zone aware, never in the future, never older than twelve
  hours — `intake.MAX_SCAN_AGE`) and derives the date in `SITE_TIMEZONE`. The window governs only accepting new
  quantity: a committed receipt replays even after twelve hours.
- One shared PN advisory lock (`part_numbers.acquire_part_number_lock`) serializes every command that can give a PN
  active quantity (release, receipt, an Undo that reopens a flow) or active demand (a Work Order save that adds or
  raises a line, an allocation reversal, the receipt); several PNs are locked in ascending order. Global lock order:
  PN advisory → demand ascending → Work Order → flow → station → Machine → Area → Operation.
- A receipt is not reversible from a station: the Movement-level reversal never rewrites the business demand the
  receipt created or raised.
- Frontend: `Receive Quantity` opens directly when the PN has no active quantity anywhere, and through the explicit
  `Receive new quantity` choice otherwise; where active quantity exists, `Confirm receipt` waits for one explicit
  acknowledgement.

**Temporary limitations.** None.

## Phase 11 — Read Models and Monitoring Views

- Production Board
- Area Board, including the Manager Summary content in its All Areas overview (no separate Manager Summary view)
- Tracking
- Management → Machines **Assigned now** per-PN quantity breakdown, replacing the Phase 6 total-only temporary presentation
- expected-duration monitoring: the canonical advisory expected-duration behavior of PROJECT_PROFILE §17 "Effective expected duration of a position" (the current Assigned Route Step's snapshot value wins, the Operation default is the live fallback, nothing is judged without either), replacing the former fixed `>= 3 days` long-dwell stand-in
- movement-derived projections
- stale-feed and long-data states

Phase 11 remains read-model / monitoring work only. It must not absorb Scan Station production writes, Priority writes, master-data management, Worker sessions, or authentication.

**Delivered.** Every monitoring figure is derived from the current-position projection and the immutable Movement
history through shared derivations (`projections.effective_positions`), so no two surfaces can disagree about a
quantity.

- **Production Board** (`app/application/production_board.py`, `GET /api/production-board`). One Department-wide read
  model (PROJECT_PROFILE §21, GUI_DESIGN §5): every PN with active quantity in the Department, or with stocked quantity
  and an open demand, with its distribution per Area, Machine and external activity, the derived holding state, the
  fixed entry time of each position (the oldest of a group; merged quantity is dated from the oldest entry of the whole
  merge and names a Machine only where all branches agree), the stocked and scrapped quantities, and the open demand
  context. Rows follow exactly the canonical demand ordering of their first open demand; a completed Work Order never
  supplies a row's context, and a row without one keeps a null due date and takes its received date from its oldest
  active flow. The Department is the single active one or the one named by `?department=<id>` on the display's address
  (none 404, several 409). The view refreshes every 5 seconds with one request in flight and shows
  `Feed stale — reconnecting` whenever no complete board is on screen.
- **Area Board** (`app/application/area_board.py`, `GET /api/area-board`). It returns the Department's active Areas with
  the same Area monitoring model the Scan Station reads (`scan_station.area_inventory`, shared schemas in
  `app/api/area_inventory.py`), plus Operations and the Stockroom's stocked lines. The model separates a quantity's
  provenance (the demand it was released for, used by the Scan Station's dialogs and the audit) from its monitoring
  context (the PN's open demands, which give every Hot rank, due date and Job Number of a monitoring row). The All
  Areas overview is PN-centric; the per-Area detail shows one row per separately actionable quantity; sorting is
  client-side.
- **PN Tracking** (`app/application/tracking.py`; `GET /api/tracking` and its detail and paging routes). The PN-centric
  list with server-side search and filters in canonical demand order, and the read-only detail: master, open demands,
  current quantity, stock and allocation, the reconciliation `introduced = active + stocked + scrapped`, every Quantity
  Flow with lineage and its `PLANNED` snapshot or `FLOATING` trace, and the Scrap, allocation and Movement histories,
  each paged on a validated keyset in a fixed order (migration `0012_phase11_tracking_index` serves the Movement
  order). The derived status `OPEN`, beside Active, Stocked and Completed, is an implementation-driven addition recorded
  in GUI_DESIGN §7.1.
- **Machines → Assigned now** (GUI_DESIGN §12.1): `assigned_lines` per PN beside `assigned_quantity`, their sum.
- **Expected-duration monitoring** (PROJECT_PROFILE §17): for a `PLANNED` flow on route, the current Assigned Route
  Step's snapshot duration; otherwise the recorded Operation's live default; with neither, nothing is judged. On route
  is decided by the arrival that established the current position, so a confirmed-deviation arrival, including a
  Repair return into an earlier step's Area, leaves the quantity off route. The server sends only the instant
  `expected_by`; the client compares it with the shared clock and shows a written `Exceeds the expected duration`
  highlight. The judgement is advisory only and never gates a command.

**Temporary limitations.** None. Monitoring views poll; there is no push (see `Deferred`).

## Phase 12 — Priority Management

- Hot WorkOrderDemand ranking
- add/search/scan (adding at the bottom applies directly)
- reorder (drag-and-drop, Move Up / Move Down)
- confirmation before removal and before every order change of existing entries — including Undo and Redo (PROJECT_PROFILE §21 Priority Management)
- audited application after explicit confirmation
- Undo/Redo
- automatic removal of an entry whose demand becomes inactive; typed-confirmed removal of a Hot demand line (owner decisions of 2026-10-04)

**Delivered.**

- Persistence (migration `0013_phase12_priority`): a pre-check that refuses, rather than renumbers, existing ranks
  that are not exactly `1..N`; then the positive-rank CHECK, the UNIQUE on `work_order_demands.priority_rank` and an
  expression index on the Hot-list audit metadata for the idempotency lookup. The Hot list is the ranked set of demand
  lines (`priority_rank IS NOT NULL`; no flag and no table). Its ranks are always exactly `1..N`, unique and dense.
- The command `POST /api/hot-list/changes` (`app/application/hot_list.py`, rules in `app/domain/hot_list.py`) applies
  one change (`ADD` at the bottom, `REMOVE`, `MOVE_UP`, `MOVE_DOWN`, `DRAG`, `UNDO`, `REDO`) against the
  `expected_order` the manager confirmed, renumbering in one transaction under the Hot advisory lock. A stale order is
  a 409 `hot_list_changed` carrying the current entries. An added demand must be eligible: not ranked, its Work Order
  not completed, and active (`requested_quantity > allocated_quantity`). The command is idempotent per
  `device_event_id`; its audit rows are the idempotency record. Every rank change appends one `UPDATED`
  `WorkOrderDemand` audit row with an identity snapshot.
- Reads: `GET /api/hot-list` and `GET /api/hot-list/candidates` (search limited to 50 with a `truncated` flag, or all
  eligible demand of a scanned `PF:PN:` barcode). Every read and the command resolve exactly one active Department.
- Automatic removal: an allocation or a quantity-lowering Work Order save that makes a ranked demand inactive removes
  it from the Hot list in the same transaction, closing the gap and auditing every shifted rank (`AUTO_REMOVE` with a
  cause block). A reversal or a quantity increase re-adds nothing. Deleting a ranked demand line requires
  `?confirm_hot_removal=true` (otherwise a 409 with the entry, nothing written); Management → Work Orders asks for a
  typed PN confirmation naming its rank (audited as `LINE_DELETE`). The shared building block is
  `app/application/hot_ranks.py`.
- Global lock order: PN advisory locks (ascending) → the Hot advisory lock → a Scan Station row → demand rows in one
  ascending pass → Work Order rows ascending.
- Frontend: Management → Priority (`views/priority/PriorityView.tsx`) renders only the server's list, confirms every
  order change with before and after snapshots, offers drag-and-drop, Move Up and Move Down, a one-field Add dialog
  (search, or a `PF:PN:` scan; a barcode with several eligible demands opens a pick), and the unlimited session
  Undo/Redo (PROJECT_PROFILE §21 item 9) as a client-side stack re-based on the current list. Writes are blocked while
  disconnected, in flight or with an unknown outcome; an unknown outcome offers a retry of the identical request.
- The PROJECT_PROFILE §27 "Hot and priority demand" report is satisfied by the Priority view, the Production Board Hot
  ranks and PN Tracking's Hot only filter.

**Temporary limitations.**

- One rank space for exactly one active Department: every read and the command refuse when no or several active
  Departments exist. This is a recorded owner decision, not a deferral: demand carries no Department, and
  per-Department rank spaces would need a Department on demand or a per-Area derivation plus a renumbering migration,
  so it is not reversible without data repair. The supported deployment has a single active Department
  (PROJECT_PROFILE §22).
- A Hot entry whose demand became inactive can exist only on a database that ran the earliest Phase 12 code before
  automatic removal existed. The Hot command still accepts `REMOVE` and `MOVE` for such an entry, the Priority view
  flags it, and Undo cannot add it back. Reconciliation check (i) lists such entries (DEPLOYMENT §5); remove them in
  Management → Priority.
- As a documented exception, the rank of a demand on a completed Work Order can still change (with audit rows) through
  the automatic removal inside the completing allocation and through the manager's removal or move of such a leftover
  entry.
- The Hot lock and the PN locks are 64-bit advisory keys. A hash collision between them could in theory invert the
  lock order; PostgreSQL would then abort one transaction (`40P01`) with nothing committed (probability about
  N/2^64).

**Not built (no canonical requirement).** Server-side Undo/Redo history (the session history is client-only by
PROJECT_PROFILE §21 item 9); push or polling of the Hot list (an open view learns of an automatic removal through the
stale-order refusal and the refresh on activation); an `is_hot` flag; consolidating the several implementations of the
canonical demand order. Priority entry at Work Order intake is listed under `Deferred`.

## Phase 13 — Full Administration and Production Identity Configuration

Completes the Administration view beyond the Phase 3.5 minimum environment setup and makes the remaining production master-data/configuration surfaces real.

- Workers — profiles with stable id, name, badge barcode (the company's existing employee badge, matched exactly after canonicalization — trim, uppercase; no `PF:WORKER:` barcodes), avatar, and active status; Workers remain separate from Users
- Worker identification per Area — persist and manage the canonical modes `disabled`, `fixed Worker`, and `scanned Worker Session`, including the configured fixed Worker where applicable
- Worker Session runtime integration — badge resolution/switching, the Scan-Station-scoped sliding inactivity session, expiration blocking modal, preservation of open production-dialog drafts, and timeout refresh only on valid production interactions
- production audit identity — add/use the canonical Worker/ScanSession references required by Scan Station Movements and Worker attribution required by station allocations; existing historical records remain valid with null identity and are never backfilled by guessing
- Route Templates — real management through **Management → Planned Routes** (GUI_DESIGN §13), permission-based for authorized production roles; Administration keeps no duplicate Route Templates screen
- Part Numbers — real management of the optional PartNumber master through **Management → Part Numbers** (GUI_DESIGN §14), including metadata editing, image management, the full barcode-label surface, and hard deletion of the metadata record only; deletion never cascades into WorkOrderDemand, QuantityFlow, PartMovement, Allocation, or history, and belongs here rather than Phase 16
- when Part Numbers management becomes real, retarget the Work Orders demand-line PN control to the shared `Edit Part Number` dialog; the barcode label remains reachable from inside it and the control uses the approved edit affordance
- Users and roles / authorization management — configuration is created here; enforcement is Phase 14
- Worker Session policies — one default sliding inactivity timeout with per-Area overrides, plus three independent badge-confirmation options for `DONE`, `QUEUE`, and `UNDO` (default ON) that choose the form of the always-present final confirmation gate
- Undo reason policy — the configuration that activates PROJECT_PROFILE §16 `reason when configured`; when enabled the backend Undo command, not only the UI, requires it
- correction permissions — configured here and enforced in Phase 14
- Department display settings — per-Department Production Board rotation timing (seconds per displayed row and minimum page dwell)
- Due Soon policy settings — replace the temporary frontend default with the configurable policy used by due-date urgency presentation, following GUI_DESIGN §3.12 rather than creating a second policy model
- theme persistence per User and per Scan Station with precedence User → Station → Dark default — the Scan Station tier is delivered here and the User preference is stored here; it is applied and saved by Phase 14
- retention/archival policy settings — the Movement-history retention period (archival and purge, and the data-size threshold with its trigger, executed in Phase 16)

Phase 13 leaves no development-only Worker-session policy preview or fake production identity in the production
module graph, and no development-only mock view remains registered.

**Delivered.** Migrations `0014` to `0028`; every Administration section and Management sub view is real.

- **Workers** (`app/application/workers.py`; `/api/workers` with its avatar routes). A Worker has an identity id, a
  name that is not unique, a badge, an optional avatar and an active flag; Workers are deactivated, never deleted.
  Badge rule (owner decision): a badge is trimmed and converted to UPPERCASE both when stored and when a scan is
  matched, so matching is case-insensitive; an empty value or a value starting with `PF:` is refused; at most 128
  characters; unique across all Workers, inactive ones included (PROJECT_PROFILE §10). The database CHECK compares
  ASCII only under the `"C"` collation; the full Unicode uppercase is owned by the domain rule. Image rule (owner
  decision): images are stored in PostgreSQL, PNG, JPEG or WebP up to 2 MiB, validated in one place
  (`app/application/images.py`), uploaded as the raw request body, served with an `ETag`, and resized by the browser
  to a 1024 px longest edge before upload. Editors send only changed fields.
- **Configuration audit.** Every effective configuration write — Departments, Areas, Operations, Scan Stations, the
  Asset Tag format, Machines, Workers, application policies, Part Numbers, Planned Routes, Users and Roles — appends
  exactly one `CREATED`, `UPDATED` or `DELETED` `audit_events` row in the same transaction, after locking the row it
  edits so `before_data` is the committed predecessor. A Machine retirement or reactivation gets an audit row only
  for the configuration delta it carries, linked by `metadata.machine_lifecycle_event_id`. No-ops, refused writes and
  lost races append nothing (lost races answer 409, never 500); there is no backfill. A child configuration write
  locks its parent Department or Area `FOR SHARE` and re-reads the parent's active flag under the lock, so a concurrent
  parent deactivation has one serial outcome. The four canonical PN CHECKs compare under the `"C"` collation, so the
  database refuses ASCII lowercase, ASCII whitespace and the empty string independent of the operating system, while
  the full Unicode rule stays in `normalize_part_number`.
- **Worker identification per Area.** `areas.worker_identification_mode` (`DISABLED`, `FIXED`, `SCANNED`; default
  `DISABLED`) with `areas.fixed_worker_id`, and the nullable `part_movements.worker_id` and
  `work_order_allocations.allocated_by_worker_id`. `app/application/station_identity.py` applies the mode in one place:
  every Scan Station command resolves the identity after its locks and after the idempotency fast path and stamps the
  same Worker on every row; Management rows and earlier history keep NULL, never guessed. A Fixed Worker cannot be
  deactivated. History shows the Worker's current name.
- **Worker Sessions** (`app/application/worker_sessions.py`). The `application_policy` singleton holds the default
  sliding inactivity timeout (owner decision: 15 minutes, configurable from 1 to 720 minutes) with a per-Area override.
  `worker_sessions` allows one open session per station; `part_movements.scan_session_id` links each Movement to its
  session through a composite foreign key. A badge scan signs in, switches or refreshes; every valid Scanned-session
  command and every successfully resolved PN or Machine scan moves the timeout forward, while a refusal, an invalid
  scan or a replay never does. A mode change away from `SCANNED`, a station rebind or deactivation, and a Worker
  deactivation close the affected sessions. The Scan Station shows the session pill with a countdown and the blocking
  `Worker sign-in required` / `Worker session expired` modal above any open dialog, with drafts preserved.
- **Badge-confirmation gates.** Three policy options (default on) for `DONE`, `QUEUE` and Undo. The server decides the
  final gate (`final_gates` in the station context): `BADGE` in a Scanned-session Area whose option is on, else
  `QUESTION`. The commands accept `confirming_badge` and refuse with 409 `badge_confirmation_not_expected`, 409
  `badge_confirmation_required` or 422 `badge_not_recognized`, recording nothing; an accepted badge signs its Worker in
  and is recorded on every row. The badge joins no fingerprint, so a committed command replays whatever the badge or
  policy is at retry time. Each option saves on its own, so concurrent administrators never overwrite each other's
  other options.
- **Undo reason policy** (`/api/policies/correction-permissions`): one global switch, default off. While on, an Undo
  without a reason is 409 `undo_reason_required` with nothing recorded; the Scan Station shows a required `Reason`
  field and restates it in the final gate. Every free-text reason containing U+0000 is refused with 422.
- **Part Numbers management** (`app/application/part_numbers.py`): optional name, current revision and ERP id
  (trimmed, blank stored as NULL, not unique) and the PN image under the image rule; a create-only `POST`, `PATCH`, the
  hard `DELETE` (metadata only — it never reads or writes demand, flows, Movements, allocations or history), the image
  routes and the bounded management list `GET /api/part-numbers/page`. The PN never travels in a path segment. The
  create takes the PN advisory lock, so it serializes with create-on-first-use; edit, image and delete lock only their
  own row, so production never waits on them. The shared `Edit Part Number` dialog is also opened by the Work Orders
  demand-line PN control, and the barcode label stays reachable from it.
- **Planned Routes management** (`app/application/route_templates.py`): create, full replace, archive (ever-used
  templates only, no unarchive), delete (never-used only) and usage; an advisory preferred Machine per step, copied
  into the snapshot without a foreign key. Every step needs an active Area, an Operation, and a non-retired preferred
  Machine of that Area; step 1 is never in a terminal Area; every step is validated on every save. Editing never
  changes an Assigned Route. Release and receipt lock every Area of the snapshot in one ascending pass, then its
  Operations, before the snapshot INSERT.
- **Department display settings and the Due Soon policy.** Per-Department rotation timing (seconds per row, default
  3, 1–60; minimum page dwell, default 6, 1–300) and the Due Soon policy (`/api/policies/due-soon`: minimum days 2,
  lead-time percentage 15, maximum days 7 by default, minimum ≤ maximum). Every due countdown uses the server policy;
  when the policy cannot be read the Scan Station keeps every production action and only withholds the
  `N days left` tone behind an explicit notice.
- **Scan Station theme tier** (`PUT /api/scan-stations/{id}/theme-preference`): the station's own Dark/Light
  preference, not audited.
- **Retention period** (`/api/policies/data-retention`): whole months from 12 to 1200, or none (the initial value).
  It is stored configuration only: no code outside the policy section reads it (a static source-scan test enforces
  this), and nothing is archived, deleted, scheduled or previewed. Administration → History archival & purge states
  that runs are not available yet; Machine assignment is a read-only statement of the two Area modes; Scan behavior is
  shown as not available yet.
- **Users, roles and permissions as configuration.** `roles`, `role_permissions` (exactly the 35-key vocabulary, one
  key per PROJECT_PROFILE §20 capability, defined as `Permission` in `app/domain/enums.py`; keys are never renamed)
  and `users` (canonical lowercase login name, display name, exactly one role, optional avatar, stored theme
  preference, active flag; no link to `workers`). The seeded roles hold exactly the initial grants PROJECT_PROFILE §20
  states (Administrator 17, Manager 10, Operator 10), with nothing inferred. Owner decision: roles are named and
  editable, permission keys are the only authority, no role is an implicit superuser, and no behavior is keyed to a
  role name. There is no role or user delete and no role deactivation.

**Temporary limitations.**

- The Scan Station header pill and the summary `Worker` row show the station context as last read (page load, every
  resolved scan, every row action opened, every command or refusal). If Administration changes the Area while a dialog
  is open, the summary may name the previous configuration, but the server records the identity it judges at
  confirmation, and the next resolve or command refreshes the station. No polling is added.
- An expired session stays open in the table until the next sign-in or configuration close at its station; readers
  treat `expires_at <= now` as ended, and a report must join Movements to sessions through `scan_session_id`, never by
  time window. One Worker may be signed in at several stations. There is no Worker sign-out control (owner decision);
  a session ends on a switch, on expiry or on an invalidating configuration change.
- Owner-visible behavior the owner may veto: Machine administration writes lock first, so a concurrent maintenance
  start or clear, an edit losing to a retirement, and a concurrent reactivation answer 409 with existing messages.
- A Part Numbers delete that commits while a Work Order save is in flight can leave the new demand without saved
  details; production never depends on the master, and the next create restores details. A Management create racing a
  production create-on-first-use serializes on the PN advisory lock.
- A Planned Route saved just before an Operation deactivation or Machine retirement keeps the stale reference, shown
  `(unavailable)` and replaced on the next save; a release or receipt and an edit of the same template, or a template
  save and an Area deactivation or Machine retirement, may wait briefly for each other; PN Tracking labels a flow's source Planned Route with the template's current name while showing the
  release-time snapshot steps. A legacy template whose step 1 is in a terminal Area stays listed and is refused at
  release until edited.
- `POST /api/route-templates` and `POST /api/areas` are not retry-safe: after an unknown outcome the client reloads and
  asks the user to check; a duplicate route is a never-used template that can be deleted. The Area case is listed
  under `Deferred`.
- PNs containing about 27 Unicode-version-sensitive code points are stored in the canonical form of the backend's
  Python Unicode database, pinned to version `15.0.0` by `test_unicode_database_version_is_pinned`; a newer browser
  could pre-normalize those code points differently, so two forms could be stored as distinct PNs. The fix of the
  entry paths and the upgrade checks are owned by Phase 16.
- The refusal message of the badge-confirmation migration's downgrade names badge confirmation more broadly than
  needed; committed migrations are not edited.

**Open follow-ups.**

- PN entry canonical form and the platform-upgrade identity checks (PN, Worker badge and Asset Tag prefix) — Phase 16.
- Tracking's ` · no Part Number master record — history unaffected` suffix could become
  ` · no saved Part Number details — history unaffected` in an owner copy pass (optional).
- Not built, no canonical requirement: ERP lookup or validation of the ERP id; the PN name or revision in the Scan
  Station resolution, the Area Board (GUI_DESIGN §6 defines no placement) and the Add Part picked-PN header; a revision
  in the Add Part results; searching Tracking by PN name; a PN archive or soft delete; Planned Route versioning, history
  views and unarchiving; a check of template references on Area, Operation or Machine deactivation; other display
  settings, a per-Department Due Soon policy and a configurable board refresh period; clearing a saved theme preference,
  administrator editing of a station's theme and a first-paint theme hint (a Light Scan Station paints Dark until its
  context loads); gates on actions other than `DONE`, `QUEUE` and Undo; a length limit
  or database-level enforcement of the Undo reason; Machine sessions; several roles per User and a link between Users
  and Workers.
- The remaining Phase 13 follow-ups (Worker on allocation rows, session history, mock Scan Station removal, badge-gate
  marker in Tracking, Undo reason categories, preferred-Machine suggestion, unique route names, release-time template
  name, inactive Areas on route steps) are listed under `Deferred`.

## Phase 14 — Authentication, Role Enforcement, and Authorized Management Corrections

- authenticate application Users; Worker badges remain Scan Station audit identity and never become login credentials
- enforce authorization server-side on every Management, Administration, correction, allocation-adjustment, and master-data write — frontend visibility is never the security boundary
- apply the correction permissions configured in Phase 13 to Undo/correction commands and other privileged production actions
- make the Phase 10 **Management allocation / allocation-reversal** capability a real authorized Management workflow: allocate stocked quantity left for later, inspect allocation history, and append auditable reversals with the required reason; never edit/delete allocation history
- implement the explicitly authorized allocation-correction path permitted by PROJECT_PROFILE §8.12 / §18 when a correction may exceed the ordinary remaining-demand limit; it is a distinct privileged, auditable intent and never a relaxation of routine Stockroom allocation
- implement the **AssignedRoute adjustment** workflow for a selected `PLANNED` QuantityFlow (PROJECT_PROFILE §8.10 / §17): authorized user only, explicit reason, previous route state preserved in audit history, selected flow only, Route Template and immutable actual Movement history untouched, and no silent route rewrite after a deviation, using the canonical route-adjustment audit representation (`ROUTE_ADJUSTED`)
- attach authenticated User identity to Management/Admin audit records and Machine lifecycle actions where canonical actor linkage applies; Scan Station production Movements continue to use Worker identity from Phase 13
- provide a read-only, **PN-scoped audit trail** in Tracking (`View audit trail`; PROJECT_PROFILE §21 Tracking "correction history", §28): the recorded changes to the PN's master data, to the Work Orders and Work Order Demand lines that request it (priority / Hot list changes and Work Order completions included), its Management allocation entries, every allocation reversal (Stockroom reversals included) and authorized beyond-demand corrections, and the route adjustments of its Quantity Flows — newest first, with time, user and reason; production Movements (Undo included) and routine Stockroom allocations stay in their own immutable histories; nothing in it is editable
- broader Manager/Admin correction capability uses explicit typed/auditable commands; Phase 14 creates no generic edit/delete path for PartMovement history
- provide the credential model for application Users; `users.login_name` is the account name sign-in uses
- bootstrap the first Administrator account and protect against removing the last holder of `MANAGE_USERS_AND_ROLES` (and of `MANAGE_CORRECTION_PERMISSIONS`) once enforcement starts
- map every enforced action to the Phase 13 permission keys; new capabilities use existing keys or add new ones by widening the vocabulary — keys are never renamed; changes to the four correction permissions (`UNDO_RECENT_SCANS`, `PERFORM_QUANTITY_CORRECTIONS`, `EDIT_WORK_ORDER_ALLOCATION`, `PERFORM_HISTORICAL_CORRECTIONS`) are authorized by `MANAGE_CORRECTION_PERMISSIONS` and changes to every other key, to roles and to users by `MANAGE_USERS_AND_ROLES`, enforced server-side
- define how the Scan Station capabilities and the Undo permission apply, given that the Scan Station is operated by Workers, who are not Users and hold no role (PROJECT_PROFILE §7, §16, §20)
- enforce permission keys as the only authority, as PROJECT_PROFILE §16 and §20 state: no implicit superuser and no behavior keyed to a role name; a canonical sentence naming a role names that role's initial grant
- resolve the theme's User tier (GUI_DESIGN §2.1) for the authenticated User from the stored preference, above the Scan Station tier, and decide which tier the toggle saves while a User is signed in

A route deviation already recorded on the actual `TRANSFERRED` Movement is not duplicated merely to manufacture another deviation event. `ROUTE_ADJUSTED` audits a later authorized AssignedRoute change; actual Movement history remains authoritative.

**Delivered.**

- **Sign-in** (migration `0029_phase14_sign_in`). Local accounts (owner decision): `user_credentials` holds a scrypt
  hash, the temporary-password flag, the failure counter and the lock; `user_sessions` holds a digest of an opaque
  token, never the token. Administration → Settings → User sign-in configures expiry (default on, 30 days, or never;
  absolute from sign-in), the lock (10 failed sign-ins lock for 15 minutes) and a new password at first sign-in
  (default on); the configurable ranges are 1 to 365 days, 3 to 100 failed sign-ins and 1 to 1440 minutes. Passwords have 6 to 256 characters after NFKC normalization and no composition rules. API:
  `GET`/`POST`/`DELETE /api/session`, `PUT /api/session/password`, `PUT /api/users/{id}/password` (temporary password;
  ends the User's sign-ins and clears a lock) and `GET`/`PUT /api/policies/sign-in`. Every write needs the header
  `X-PartFlow-CSRF: 1`; the session cookie `partflow_session` is `HttpOnly`, `SameSite=Strict` and `Secure` when
  `SESSION_COOKIE_SECURE` is set. Sign-ins and failures are logged, not audited.
- **First Administrator and recovery.** While no Administrator exists (an active User with a password whose role holds
  `MANAGE_USERS_AND_ROLES`), `POST /api/setup/administrator` accepts a one-time setup token the server prints to its
  log; the creation is atomic and closes setup, and the first Administrator's role must hold both
  `MANAGE_USERS_AND_ROLES` and `MANAGE_CORRECTION_PERMISSIONS`. `python -m app.cli reset-password --login-name <name>`
  resets an existing password only while an Administrator exists and never creates one;
  `python -m app.cli restore-correction-permission-management --role-name <role>` repairs a database with no holder of
  `MANAGE_CORRECTION_PERMISSIONS` (the server logs a startup warning in that state).
- **Enforcement.** The route registry `app/api/route_access.py` classifies every route as `PUBLIC`, `STATION`,
  `SIGNED_IN` or `PERMISSION`, and a test fails on any unlisted route. Public reads are the Production Board, the
  Station Selector lists, `GET /api/machines`, `GET /api/route-templates`, the image reads and
  `GET /api/policies/due-soon`. Administration reads need a signed-in User; Management reads need
  `VIEW_PRODUCTION_DATA` or a key whose action the view hosts. Every Administration, master-data and Management write
  needs its own permission key (for example `MANAGE_AREAS`, `MANAGE_MACHINES`, `MANAGE_PART_NUMBER_MASTER`,
  `MANAGE_WORK_ORDERS`, `EDIT_WORK_ORDER_DEMAND`, `CONFIGURE_SYSTEM_SETTINGS`, `MANAGE_USERS_AND_ROLES`). A Work Order
  save is judged by its content (number or due date versus demand lines), and a Hot list change by its effect (adding
  or removing members needs `SET_DEMAND_PRIORITY`, reordering `REORDER_HOT_ITEMS`, Undo and Redo included); automatic
  Hot removals inherit the triggering command's key. Badge values are returned only to holders of `MANAGE_WORKERS`. A
  refusal is 403 `permission_denied` with the sorted `required_permissions` (with `any_permission: true` for a read).
- **Permission-management guard.** Granting or revoking a correction permission or `MANAGE_CORRECTION_PERMISSIONS`, and
  changing the role, activity or password of a user whose role holds one, also needs `MANAGE_CORRECTION_PERMISSIONS`.
  The last-holder rule refuses a change that would leave no active user with a password holding
  `MANAGE_USERS_AND_ROLES`, or none holding `MANAGE_CORRECTION_PERMISSIONS` (409 `last_permission_holder`). Role and
  user writes serialize on the advisory lock `partflow:user-administration`.
- **Actors.** Every audit row, Machine lifecycle event and Management allocation row carries `actor_user_id`; a release
  records the User in its `RECEIVED` Movement context; the legacy text actor columns are no longer written and never
  backfilled; no Scan Station Movement carries a User. A Management command
  replayed by a different User than the one that recorded it is refused with 409 `recorded_by_another_user`.
- **Scan Station devices** (migration `0030_phase14_station_devices`; owner decisions). Only an enrolled device may
  call the Scan Station routes, and each station command needs the permission of the role applied at Scan Stations
  (`application_policy.scan_station_role_id`, initially the seeded Operator role). An administrator issues a one-time
  enrollment code for one station (valid 15 minutes, single use); the station browser exchanges it for a device token
  sent in the header `X-PartFlow-Station-Device`; a device can be revoked. Only digests of codes and tokens are stored.
  Refusals: 401 `station_device_required`, 403 `station_device_mismatch`, 409 `station_context_changed`, and 403
  `station_permission_denied` (judged after the idempotency re-check, so a committed command still replays). Enrolling
  needs `MANAGE_SCAN_STATIONS` and, while the station role holds a protected permission,
  `MANAGE_CORRECTION_PERMISSIONS`; revoking needs `MANAGE_SCAN_STATIONS` only. A device never identifies a Worker, and
  a badge never authorizes. The Scan Station hides actions the station role does not grant.
- **Management allocation and the beyond-demand correction** (migration `0031_phase14_beyond_demand`). Management
  allocates stock left for later (`POST /api/allocations/management`), reverses (`POST /api/allocations/{id}/reversals`,
  Management-only) and records the authorized correction (`POST /api/allocations/corrections`: one line, more than its
  remaining demand, never above the available stocked quantity, mandatory reason, recorded `exceeds_demand`,
  reversible). All need Edit Work Order Allocation (`EDIT_WORK_ORDER_ALLOCATION`); the Stockroom keeps only its
  receiving confirmation. The shared `Adjust WO Allocation` dialog is offered by Work Order Details on every saved line
  of Open, Released and Completed Work Orders, and by Tracking's Corrections section.
- **AssignedRoute adjustment** (migration `0032_phase14_route_adjusted`).
  `POST /api/quantity-flows/{quantity_flow_id}/route-adjustments` (`ASSIGN_ROUTES`) replaces only the future steps of
  one `ACTIVE` `PLANNED` flow's own AssignedRoute — the steps after the last step any Movement of the flow references,
  undone Movements included — with a mandatory reason, a stale-route guard (409 `route_changed`) and a refusal of an
  unchanged tail. It writes one `ROUTE_ADJUSTED` audit row on the `AssignedRoute` with the complete previous and new
  step lists, no Movement, and never touches the Route Template or another flow. PostgreSQL forbids any UPDATE of
  `assigned_route_steps` and the deletion of a referenced step. Station Undo refuses a command that created a flow
  whose route was adjusted afterwards. Tracking offers `Edit assigned Route…`.
- **PN audit trail** (`app/application/audit_trail.py`; `GET /api/tracking/audit-trail`): a read-only reader, newest
  first, over the audit rows of the PN's master record, of the Work Orders and demand lines that request it (deleted
  lines marked `(since deleted)`) and of the route adjustments of its flows, plus its Management allocation rows and
  every allocation reversal. Tracking's Corrections section adds `Change priority` (a hand-off into Management →
  Priority) and `View audit trail`.
- **Theme User tier** (`PUT /api/session/theme-preference`; owner decision). Precedence User → Scan Station → Dark on
  every route. While a User is signed in the toggle saves the User preference on every route; the station preference
  is saved only while the browser knows nobody is signed in; sign-out returns to the station preference on station
  routes and Dark elsewhere. Theme changes are not audited. Worker Sessions never affect the theme.

**Temporary limitations.**

- The seeded Administrator role holds neither `VIEW_PRODUCTION_DATA`, `MANAGE_WORK_ORDERS` nor the two priority keys
  (owner decision: keys only, seeds unchanged), so until an administrator grants them in Roles & permissions it cannot
  open Area Board or Priority, create Work Orders or release.
- First-run setup is protected only by the setup token printed in the backend log.
- Device tokens and session cookies are bearer credentials and must travel over HTTPS, which the platform proxy
  provides on the production host (Phase 16). A revocation applies from the next request.
- Audit rows written by a station or before sign-in existed show no user, because the stored row cannot tell a station
  edit from an older Management edit.
- Management writes judge the permissions read at the start of the request; only role, user and password writes
  re-read them under the lock. A Management command waiting on a login-name rename of its acting user waits while
  holding its advisory locks: a bounded stall with no cycle.
- Without the master-data key the Part Number dialog opens read-only (the label stays reachable) and Planned Route
  and Machine rows do not open, so a read-only Planned Routes user sees the Area chips but not a step's Operation,
  estimate, preferred Machine or instructions.
- The audit-trail read has no dedicated index: the deleted-line lookup scans the `WorkOrderDemand` audit rows. Its
  measured time is well below the 100 ms trigger; an index is added if a page read exceeds it.
- Two flows with different routes, for example siblings after one was adjusted, refuse to combine until both carry the
  same route.
- Owner-visible theme defaults the owner may veto: a User left signed in on a terminal's browser governs its theme
  until sign-out (done from standard mode, because the account chip is hidden in production mode); while the sign-in
  state is not yet known the toggle is session-only; the station toggle also saves in standard mode when nobody is
  signed in (the alternative is to save only in production mode).

**Open follow-ups.**

- The permission key that authorizes Movement-history archival and purge execution — Phase 16.
- Not planned, no canonical requirement: an audit row for a demand-line deletion, for a completion caused by an
  allocation or for a reopen caused by a reversal; user sign-in at the Scan Station; recording the device on Movements
  or allocations; an editor for which role is applied at Scan Stations (edit that role's grants instead); per-station
  or per-device permission sets; showing which stations a browser is enrolled for; rate limiting of enrollment-code
  attempts; a multi-line PN-level Management allocation screen; an `audit_events` row for
  allocation commands (the allocation row is its own audit record); adjusting a completed Work Order's allocation from
  Tracking (use Work Order Details); editing past or referenced route steps, changing a Route Template or another flow
  through an adjustment, adjusting a Floating, closed or completed flow, an automatic route update after a deviation,
  an inherited `adjusted` marker on a split child or merge result, and an expected-step precondition on the station
  deviation confirmation; a system-wide audit reader for configuration entities, Users, Roles, devices, Machines and
  Planned Routes; production Movements and routine Stockroom allocations in the trail; the Stockroom Worker of a
  station reversal row; a deep link into Priority or the trail; auditing reads of the trail; a Hot list actor in the
  Priority view itself; clearing a saved User theme, showing or editing it in Administration, and live theme sync
  between open browsers.
- Directory or single sign-on (SSO) sign-in, multi-factor authentication, self-service password reset, the Tracking
  `Quantity adjustment` and historical-correction commands, and the permission keys no route requires yet — listed
  under `Deferred`. Returning stocked quantity to production stays an open owner decision (PROJECT_PROFILE §32).

## Phase 15 — File-Based Work Order Import

- idempotent import
- row validation
- clear partial-failure reporting
- no ERP dependency

Owner decisions:

- File format: CSV (UTF-8) **and** Excel (`.xlsx`), the same fixed column headers and limits for both, with downloadable templates; reading `.xlsx` justifies the `openpyxl` dependency, and only stored values are read.
- Updating **active** (Open / Released) Work Orders from a file is allowed, but only after the UI lists every change per Work Order and the user types a confirmation; the update follows the existing Work Order edit rules through the single existing write path, and a completed Work Order is never changed (reported, not touched). A Work Order Number is required on every imported row.
- The atomic unit is one transaction per Work Order: valid Work Orders commit, refused ones are listed per Work Order with reasons.
- `Check file` (preview) is required before `Import`; the import re-validates the same bytes, and the typed confirmation for updates happens at Import.

**Delivered.** No migration; new dependencies `openpyxl` (runtime) and `types-openpyxl` (development).

- Management → Work Orders offers `Import from file…` (GUI_DESIGN §11.7) for a UTF-8 CSV file or the first visible
  worksheet of an `.xlsx` file (stored values only; formulas are never evaluated; columns A to BL are read).
- Routes: `POST /api/work-orders/import/preview` (`Check file`: a dry run with no lock and no write, classifying each
  Work Order `WILL_CREATE`, `WILL_UPDATE`, `EXISTS` or `REFUSED` with the values read per line, and returning a
  `check_token` and, for updates, an `update_token`); `POST /api/work-orders/import` (re-validates the same bytes — a
  file whose hash differs from the `X-PartFlow-Import-Check` token is refused — and reports `CREATED`, `UPDATED`,
  `EXISTS` or `REFUSED` per Work Order); and the header-only templates `GET /api/work-orders/import/template.csv` and
  `.xlsx`.
- Columns: Work Order Number, Part Number and Requested Quantity are required; Job Number and Due Date (`YYYY-MM-DD`)
  are optional. A row without a Work Order Number blocks the whole import; source bill-of-materials (BOM) columns
  refuse the file. Limits: 1 MiB, 2,000 data rows, 500 lines per Work Order, 200 characters per text cell. A Work Order
  with any invalid row is refused whole while the others import. Replaying a file creates nothing: the Work Order
  Number is the idempotency key.
- New Work Orders are created through the existing `create_work_order` write path, each in its own transaction. A Work
  Order created without a received date (every imported one) takes today's date on the site calendar
  (`SITE_TIMEZONE`). The shared quantity validator refuses values beyond the PostgreSQL integer range with 422 on the
  manual path as well.
- Updates of Open and Released Work Orders, matched by canonical PN: a quantity (never below the released or
  allocated floor), a due date, an appended Job Number, and new lines on an Open Work Order. The consequences (leaves
  the Hot list, completes the Work Order, new Part Number) are flagged before confirmation. An unchanged active Work
  Order is `EXISTS` ("Already in PartFlow — nothing to change"); a completed one is never changed; a change that breaks
  an edit rule (a new line on a Released Work Order, a quantity below the floor) is `REFUSED` with its reason. Empty
  cells and lines missing from the file keep the saved values, and an import never removes a line. Import with at
  least one update opens a typed confirmation (`CHANGE {m}`) listing every change and sends it as
  `X-PartFlow-Import-Confirm` (a malformed value is 422). When the commit's own change list differs from the confirmed
  one, every update of the file is refused per Work Order while the creates still import; a Work Order that changed
  after the Import re-checked it is refused by a state token; nothing unconfirmed is ever written. Each update is one
  transaction through the single `update_work_order` write path, so the manual edit rules, Hot-list removal and
  completion judgement apply.
- Audit: the Work Order `CREATED` row and the demand `CREATED`/`UPDATED` rows carry
  `metadata.intake = {"channel": "FILE_IMPORT"}` and the signed-in User; an import writes no Work Order header row for
  updates.
- Permissions are judged by content: the preview and the templates need either Create and edit Work Orders
  (`MANAGE_WORK_ORDERS`) or Edit Work Order Demand (`EDIT_WORK_ORDER_DEMAND`); the commit needs the first for the Work
  Orders it creates and the second for those it changes (one 403 naming the full set).
- The `web` tier allows 4 MiB bodies and a 180-second timeout on the import route (Phase 16); the platform proxy must
  allow at least 5 MiB and 300 seconds (DEPLOYMENT §5).

**Temporary limitations.**

- Owner-facing defaults taken without a decision and open to one: a stale confirmation refuses every update of the file
  rather than only the changed Work Orders, and a lowering of quantity is allowed down to the committed floor.

**Open follow-ups.** File-import capabilities beyond the above, retention or purge of the audit rows an import writes,
and a shared length cap for Work Order Number, Part Number and Job Number on the manual path are listed under
`Deferred`, each waiting for an owner decision.

## Phase 16 — Deployment, Production Hardening, and Admin Maintenance

Operational requirements and platform paths are defined in [`DEPLOYMENT.md`](./DEPLOYMENT.md). Guidance for running the current repository as restricted internal staging does not make this phase complete.

- backups
- migrations
- HTTPS/internal access
- observability
- rollback
- reconciliation checks
- go-live on the NAS — done on 2026-11-09 with release `v1.0.0` (owner decision of the same day: direct production use, with no pilot phase and no release gates; DEPLOYMENT §1 and §5)
- production database-role hardening, including the intended application-role UPDATE/DELETE restrictions on append-only production/audit history
- platform-upgrade identity checks: canonical PN and Worker badge identity follow the backend Python's Unicode Character Database (UCD), pinned to `15.0.0`, and the Asset Tag prefix check refuses Python's `\s` whitespace set, a strict superset of PostgreSQL's libc `[[:space:]]`; before any Python upgrade that changes the UCD, or a glibc change, a read-only check lists every stored PN, badge and Asset Tag prefix whose canonical form or CHECK outcome would change, and the owner decides on re-canonicalization before the upgrade
- PN entry canonical form: the Work Orders typed-PN and demand-label paths submit the trimmed raw input and display the server's canonical form, so the browser's Unicode tables never decide a stored PN (today they can diverge from the server only on about 27 Unicode-version-sensitive code points)
- AssignedRoute snapshot copy lock order — delivered: a partial-quantity transfer locks its target Area in one ascending pass with the Areas of the snapshot its split copies (`route_templates.lock_assignment_areas`, as for release and receipt), and every snapshot copy of a split child or a merge result (`lineage.copy_assigned_route`) locks its Areas, then its Operations, `FOR KEY SHARE` in ascending id order before its INSERT (`route_templates.lock_snapshot_references`); a crossing release or a second crossing partial transfer therefore waits instead of deadlocking, which the crossing-route concurrency tests in `backend/tests/test_route_template_management_api.py` prove
- **administrative Movement-history archival/purge maintenance** (PROJECT_PROFILE §28): configurable retention period, select-by-cutoff → lossless archive export → verify → purge exactly the verified archived rows through a separate privileged maintenance path; keep reversal/reference chains and atomic-command groups whole so no retained row references purged history; include policy / size-threshold / manual triggers, scope preview, mandatory reason, and full audit; normal runtime remains append-only; no full retention/archival engine is built before this phase; the retention period is already stored by Phase 13 (`application_policy.retention_period_months`, whole months or none), and the data-size threshold's measure and unit are defined here together with its trigger

PartNumber master hard deletion is **not** a Phase 16 maintenance operation. It belongs to Phase 13 Management → Part
Numbers and removes metadata only.

**Delivered.** None of the work below added a migration; the schema head stays `0032_phase14_route_adjusted`.

### Reconciliation command

`python -m app.cli reconcile` checks production state against Movement history and never repairs anything. It takes
one read-only REPEATABLE READ snapshot after ACCESS SHARE table locks (no row or advisory locks; a 5-second lock
timeout and a 300-second default statement timeout) and runs these checks:

- (a) to (f) of OPERATIONS_RUNBOOK §7 (check (f) does not report the excess of an authorized beyond-demand correction,
  recorded `exceeds_demand`), plus the SLICE1_DATA_MODEL §17 cross-row invariants except the audit-row invariant,
  which the transaction protocol enforces;
- (g) no retained Movement references purged history — `not_applicable` until archival exists;
- (h) append-only guards and database-role privileges intact — `not_applicable` only on a database without the
  PartFlow database roles (development, test, staging);
- (i) the Hot-list query of DEPLOYMENT §5 (Hot list entries are active demand);
- (j) canonical identity under the running interpreter and database: every stored PN, Worker badge and Asset Tag
  prefix whose canonical form or CHECK outcome would change, every canonical-form CHECK re-evaluated under the running
  database collation and ctype, the collation version, and index-independent duplicate probes of the identity keys.
  The glibc and PostgreSQL-image half runs on a restore onto the candidate server (`restore-test.sh --db-image`), and
  `reconcile --check j` runs against a candidate image.

The JSON report goes to standard output; the exit code is 0 clean, 1 mismatch, 2 could not run, and it counts only
with a complete report whose `exit_code` matches. Each finding is an incident the owner decides (for example Work
Orders left uncompleted before the completion fix of 2026-10-06, inactive Hot entries, collation or index drift).

### Production images and Compose stack

The full description is in DEPLOYMENT §3.1. In short:

- The backend image's `production` stage runs as the non-root user `10001:10001` (Python 3.12, UCD `15.0.0`) with no
  reload and no migration on start. Database settings come from exactly one of `DATABASE_URL` (percent-encoding
  accepted) or the file-based `DATABASE_*` settings; a validation error never echoes an input value.
- The `web` image (`nginx:1.30.5-alpine`) serves the immutable frontend build and proxies `/api` with request-size
  limits (1 MiB by default, 4 MiB on the upload and import routes, so the application's own JSON 413 wins), a
  180-second timeout on the import route, rate limits per client address on sign-in and the password routes, JSON 413,
  429, 502 and 504 answers, security headers including the baseline content security policy, an access log without
  the station device header or query strings, and the client address taken only from one trusted hop. A 413 or 429 is
  a definite refusal; a 502 or 504 is an unknown write outcome.
- `compose.production.yaml` (project `partflow-production`) runs `db`, `backend` and `web`, plus the one-shot `migrate`,
  `db-roles`, `backup-tools` and `status` services under the `ops` profile. `web` is published on `127.0.0.1` only;
  images are tagged by `PARTFLOW_RELEASE` and never pulled or built by `up` or `run`; passwords are secret files; the
  session cookie is always `Secure`. `.env.production.example` is the configuration and secret inventory, and CI builds
  the production images and runs the static artifact tests.

### Release flow

- Release identity: the images and the frontend bundle carry the release tag and the full commit; a build refuses a
  missing or malformed value, and existing tags are never rebuilt.
- `GET /api/health/live` is liveness; `GET /api/health` is readiness and answers 503 on a schema mismatch.
- The write gate (on in production, off in development and tests) refuses every write from a page of another release
  (409 `release_mismatch`) and every write under a schema mismatch or an unconfirmable database (503 `not_ready`), with
  nothing written. `ACCEPT_SCHEMA_REVISION` accepts one verified-compatible older schema during a rollback. A station
  device's `last_seen_at` is the one documented write under a mismatch.
- `python -m app.cli migrate` upgrades in one transaction, requires a backup reference or a recorded reason, applies
  the grants in the same transaction and refuses while a backend session is connected; `python -m app.cli revision`
  reports read-only.
- `deploy/production/release.sh` builds only the checked-out tag, needs `--operator` and `--approver` (one person may
  give the same name to both), freezes writes by stopping `backend`, takes the verified pre-release backup inside the
  freeze, migrates, switches `backend` then `web`, runs `smoke.sh` and writes `record.json` (exit codes 0 completed,
  1 stopped, 2 could not run, 3 frozen, 4 needs review). It is never scheduled and refuses while a `partflow-staging`
  project exists on the same Docker daemon.
- The frontend shows the `UPDATED` notice and blocks writes on an outdated page. With no dialog open it reloads itself
  after a 10-second countdown in the notice: on Scan Station and Production Board routes the countdown starts at once,
  and on every other route the page reloads once nobody has used it for 1 minute and nothing on it is unsaved
  (GUI_DESIGN §3 rule 13).
- Rollback without a schema change switches the release tag back, so the previous release's images must stay on the
  host. A schema rollback restores the pre-release backup (below). There is never a down-migration.

### Database roles

- `python -m app.cli provision-roles` creates or repairs the roles `partflow_app` and `partflow_maintenance` from
  secret files, with no elevated attribute, no membership and no owned object, and never writes a plaintext password
  to SQL, a log or a report.
- `python -m app.cli apply-grants`, also run inside every `migrate`, derives every grant from one table classification
  (`backend/app/infrastructure/database_privileges.py`): SELECT and INSERT only on `part_movements`, `audit_events`,
  `machine_lifecycle_events`, `quantity_flow_lineage` and `work_order_allocations`; UPDATE kept on `worker_sessions`;
  DELETE but no UPDATE on `assigned_route_steps`; no TRUNCATE anywhere; PUBLIC and default privileges removed. A table
  without a class makes the command refuse. Grants are never restored from a dump; they are re-derived after every
  migration and restore.
- The production `backend` connects as `partflow_app` and never holds the owner credential; the raise-on-write
  triggers stay as the first layer, and reconcile check (h) verifies both layers from the catalogs.
- The backend tests also run under the application role (`PARTFLOW_TEST_DATABASE_ROLE=app`, the CI mode); this mode is
  the required local check before committing a change that touches database writes, grants or test setup.

### Backups, restore drill and rollback by restore

- `deploy/production/backup.sh` publishes each backup as a directory under `PARTFLOW_BACKUP_DIR` (dump, table of
  contents, `manifest.json`, `SHA256SUMS`) by one atomic rename. `python -m app.cli backup-manifest`, `backup-verify`
  and `backup-rotate` run in the network-less `backup-tools` service. Rotation keeps 14 daily and 8 weekly verified
  backups until the owner sets the real values; pre-release, manual and archive backups are never rotated.
- `migrate` refuses a pre-release backup that is stale, of another revision or database, tampered with, or not proven
  to hold every write.
- `deploy/production/restore-test.sh` restores a backup into an isolated Compose project, provisions throwaway roles,
  applies the grants, runs a full `reconcile` (checks (h) and (j) must pass) and the smoke checks, writes timed
  evidence and removes only the project it created.
- Rollback by restore (OPERATIONS_RUNBOOK §6, path 3) restores the pre-release backup into a new database
  `<POSTGRES_DB>_r<stamp>` inside the same PostgreSQL instance, keeps the migrated database, and starts the previous
  release only after the owner's approval is recorded in `rollback.json`. A restore into production never overwrites.
- Encryption and off-host replication are done by the platform tool (Hyper Backup on the NAS, or restic on a virtual
  private server (VPS)).

### Monitoring

- The production backend logs every record as one JSON line; every response carries an `X-Request-ID` that `web` and
  the backend both log. Each request writes one `app.access` record with the production context of the command (PN,
  flow, demand, Area, Operation, Machine, Worker, Scan Station, `device_event_id`) and the reason of every refusal, and
  never a request body, query string, cookie, token, badge, password or scanned value; the first-run setup token is
  the one announced exception.
- `python -m app.cli status` reports database size, connections, lock waits, Movement volume, schema readiness, backup
  age and the connected database role.
- `deploy/production/check.sh` checks HTTPS, the certificate, containers, restarts, backend errors, disk, database,
  schema and backup age and exits non-zero to reach the host scheduler's failure notification (thresholds: a backup
  older than 26 hours, less than 15 % free disk, a certificate within 21 days of expiry, any restart-count increase,
  any backend error record; a repeated failure is notified every 6 hours). `deploy/production/scheduled-reconcile.sh`
  runs `reconcile` daily.

### Go-live on the NAS

PartFlow went live on the company Synology NAS on 2026-11-09 with release `v1.0.0`. The installation followed
[`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §1 to §10, and later releases are installed with
`release.sh` from a git tag (§12 of that guide). Only what the software needs in order to work was required
(DEPLOYMENT §5): HTTPS at the platform proxy (request bodies of at least 5 MiB, timeouts of at least 300 seconds, the
certificate trusted by every workstation and Scan Station), first-run setup with one backend worker until the first
Administrator exists, each Scan Station device enrolled, no `partflow-staging` project on the production Docker
daemon, and releases from a git tag.

**After go-live, when convenient (blocks nothing).** Apart from installing releases with `release.sh`, none of these
is recorded as done on the real host yet; the scripts were run on Docker Desktop and in a Linux container.

- The platform proxy: it supplies the client address last in `X-Forwarded-For`, its body and timeout settings, the
  loopback peer matching the `edge` gateway (otherwise set `PARTFLOW_TRUSTED_PROXY`), the filtering of the station
  device header from its own logs, the certificate procedure and workstation trust, and the decision whether to send
  HTTP Strict Transport Security (HSTS).
- Secret-file permissions, measured resource limits and worker count, and the worst-case import duration through the
  real hostname.
- A rollback executed on the NAS (releases already run there); the browser checks that need a configured Area,
  Operation and enrolled Scan Station (automatic reload on a Scan Station production route, no reload while a
  production dialog or the Worker sign-in modal is open, the in-modal `Reload page`, and the `● Live` feed under the
  update notice); and an open page on any other route counting down and reloading itself once nobody has used it for
  1 minute.
- The privilege probe and `reconcile --check h` on the production database, and a record that `backend` holds only
  `partflow_app_password`.
- The backup directory, the daily backup task and the encrypted off-host copy, the first backup, a check that an
  off-host copy restores, one timed `restore-test.sh` run, the fail-closed behavior of a missing backup mount on a
  Linux engine, and a rehearsal of rollback by restore if the owner wants one.
- The `check.sh` schedule (every 15 minutes) and the daily `scheduled-reconcile.sh`, a received failure notification,
  the measured log volume, and the reconcile run time on the production database.
- The glibc and PostgreSQL-image half of check (j) on the real host, before any platform upgrade.
- The same checks on a VPS, only if one is ever used.

### Planned: Movement-history archival and purge

Archival and purge follow when the data volume needs them; nothing of it is implemented. Today the retention period is
stored but read by nothing, all Movement history stays in the database, Administration states that archival and
purge runs are not available yet, check (g) is `not_applicable`, and `check.sh` reports `archival_proposal` as
`SKIP`. The planned work has three steps: (1) the run record, the authorization, the preview, request and approval
screen, and the archive export and verification, with no deletion; (2) the privileged purge with a carry-forward
record of purged flows, so derived quantities stay correct; (3) the policy and size-threshold triggers, which only
propose runs. Constraints already fixed by the implemented work: the archive directory lies outside
`PARTFLOW_BACKUP_DIR` and is never rotated or pruned by any tool; the purge precondition is a backup checked by
`backup-verify` whose dump started after the run's verification; the maintenance role gets DELETE on `part_movements`
only, with its new trigger and function added to the pinned guard set of check (h) in the same commit as their
migration; the new run tables get a grant class and a `maintenance` service is added; and a Movement purge deletes
Movements only, never `worker_sessions` rows.

Owner decisions needed before this work starts:

- the deletion scope (proposed default: Movement rows only, with a carry-forward record of purged flows as a second
  evidence source for released, stocked and route-usage quantities, which needs PROJECT_PROFILE §13 and §28
  amendments);
- the archive format, storage, verification and durability (including off-host replication with a recorded checksum
  before any purge);
- the permission key that authorizes archival and purge execution (a new key, or an existing one such as
  `CONFIGURE_SYSTEM_SETTINGS`);
- the manual cutoff rule (no newer than the configured retention period, or a fixed 12-month floor when no period is
  set);
- the execution model (approval in the application with a reason and preview, executed by an out-of-band maintenance
  job, optionally with a second approval by the host operator);
- the meaning and unit of the data-size threshold (for example the live Movement row count);
- whether a configured policy may ever run without a per-run approval (proposed default: no);
- whether the audit rows written by file import are ever retained or purged (proposed default: deferred).

### Temporary limitations

- A database superuser (the owner role) who disables a trigger or sets `session_replication_role = replica` in its own
  session can change history and restore the guard; check (h) sees only the persistent state and cannot detect such a
  past change (accepted; a hash chain over Movement rows was not chosen because it adds a write cost to every
  production command). Objects the owner creates are outside (h) unless they replace a guard. The development, test and
  Deployment Admin staging databases keep one owner role, where the trigger alone binds every path that does not
  disable it.
- An authenticated read-back of individual screens after a restore is not automated; reconcile replays Movement history
  against every projection, and the owner may check by hand (`restore-test.sh --keep`).
- Writes made outside the application are outside the backup freshness guarantee.
- Backups hold every table, including credential hashes, so the backup directory and every copy are secret.
- Until the daily backup task runs, the only backups are those `release.sh` takes before each release.

### Open owner decisions

- Accept the implemented rollback-by-restore target: a new, empty database inside the same PostgreSQL instance instead
  of "a clean instance" (if rejected, a new volume replaces that step of the procedure; no data repair follows).
- The real backup retention counts (placeholders: 14 daily and 8 weekly) and, if wanted, an approved recovery point
  objective (RPO) and recovery time objective (RTO); the daily schedule gives an RPO of 24 hours until then.
- Whether to send HSTS from the platform proxy.
- Recommended defaults in force that the owner has not explicitly confirmed and may change: readiness answering 503 on
  a schema mismatch; the backend write gate; the two database roles and the accepted superuser limit of check (h);
  encryption and off-host replication by the platform tool; and the monitoring thresholds above.

### Open follow-ups

- PN entry canonical form (scope bullet above) — not implemented.
- Platform-upgrade identity checks: the Python side is implemented by check (j); before any Python or glibc upgrade the
  owner reviews the check's list and decides on re-canonicalization.
- Deployment Admin (the separate `pf` tool used for staging): development stopped on 2026-10-14, when the owner chose
  to rebuild the deployment kit as a separate project
  ([`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md)), so its open steps will not be built: moving its
  container health checks and frontend probes to `GET /api/health/live` (needed before staging runs a commit whose
  schema head differs from the staging database, because `/api/health` is readiness), its database-role provisioning
  and backup adoption (PF-A4.1), and a managed route for running an application command on staging (PF-A4). The
  replacement keeps the rule that `apply-grants` runs after every restore and that check (h) passes before the
  application starts.
- Movement-history archival and purge — planned above.

## Deferred

Each item below waits for an owner decision or a canonical requirement; none has an owning phase yet.

- ERP synchronization
- File-import capabilities deferred by the owner on 2026-10-06: worksheet selection; other formats (`.xls`, `.xlsm`,
  `;`-separated CSV); a column-mapping screen and header aliases; Revision, parent assembly, Work Order due date,
  received date, Request Type, requester, reason and notes columns; an import history or batch view and the file name
  in the audit; and detection of hidden or filtered worksheet rows. The update of existing Work Orders from a file
  deliberately does not offer, each until an owner decision asks for it: removing demand lines missing from the file,
  clearing a due date or Job Number with a blank cell, adding lines to a Released Work Order by import, changing a Work
  Order header field from a file, and per-Work-Order confirmation digests (which would need a non-stateless
  transport).
- Retention or purge of the audit rows a file import writes: PROJECT_PROFILE §28 scopes retention to PartMovement and
  the import keeps no import record; the Phase 16 archival decisions carry it with the default "deferred".
- A shared length cap for Work Order Number, Part Number and Job Number on the manual Work Order create and edit: the
  import refuses text cells over 200 characters, while the manual path accepts any length, so a value beyond the
  database index entry limit (about 2.7 KB of poorly compressible text) fails as an unhandled database error (HTTP 500,
  rolled back, nothing written) instead of a 422. The owner decides the shared cap before one rule is applied to both
  paths.
- offline scan synchronization (not part of the MVP; production writes stay blocked while disconnected; an open
  decision in PROJECT_PROFILE §32)
- WebSocket/SSE push connectivity (event-driven + polled health detection is the approved mechanism)
- advanced analytics
- speculative automation
- broad ERP and manufacturing execution system (MES) features
- priority entry at Work Order intake (PROJECT_PROFILE §21 Work Orders item 5, GUI_DESIGN §11.2 "priority when
  applicable") — deferred by the owner on 2026-10-04; the Hot list (Management → Priority) stays the only place priority
  is set; when picked up, an "add to Hot list" entry point through the same command, appended at the bottom (no typed
  rank), because a typed rank at intake would be a second writer of the dense list
- scan-behavior policies and general application settings beyond the Due Soon warning and the User sign-in settings —
  no canonical setting is defined (PROJECT_PROFILE §20 and §21 and GUI_DESIGN §9 only name the sections);
  Administration shows them as not available yet until the owner defines their content (owner default, 2026-10-04)
- the Tracking `Quantity adjustment` and historical-correction commands (owner decision of 2026-10-06)
- permission keys that no route requires yet, with their owners: `Manage scan behavior` (with the Scan behavior
  section, once one exists in Administration); `Resolve exceptional production situations` (until PROJECT_PROFILE
  defines a command); `Export and print reports` (until a report surface is defined); `Perform quantity corrections`
  and `Perform authorized historical corrections` (until the quantity-adjustment and historical-correction commands
  exist). Administration marks them as granting nothing yet.
- directory or single sign-on (SSO) sign-in, multi-factor authentication and self-service password reset for
  application Users (no canonical requirement; the separate credential table keeps them additive)
- Phase 13 follow-ups with no canonical requirement:
  - `POST /api/areas` retry safety: Area names are not unique and there is no idempotency key, so a fix needs an
    idempotency key, an API change or a canonical Area-name rule;
  - a display surface for the Worker recorded on allocation rows (`allocated_by_worker_id`; GUI_DESIGN §7.2 defines no
    Worker field there);
  - a surface for Worker Session history and `scan_session_id`;
  - retiring the development-only Scan Station mock preview (`ScanStationMockView.tsx`, `?preview=mock` and its mock
    datasets; never in a production build);
  - showing in Tracking whether a badge gate confirmed an action;
  - Undo reason categories or per-Area scope;
  - suggesting the snapshot's preferred Machine during Machine assignment;
  - unique Planned Route names (needs a canonical rule first);
  - snapshotting the Route Template name at release, so PN Tracking shows the release-time name;
  - admitting inactive Areas on Planned Route steps (an Application and UI rule change with no data repair; today an
    inactive Area is refused at save and shown `{name} (unavailable)` in the editor).

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-12 | uncommitted | Current State and Phase 16: PartFlow is live on the company NAS since 2026-11-09; the update notice left the unconfirmed defaults, and the Deployment Admin follow-ups record that its development stopped. |
| 2026-11-12 | `b413c96` | Phase 16 snapshot copy lock order delivered: a split or merge copy locks its Areas and Operations in ascending order, together with a partial transfer's target Area, so crossing releases and partial transfers no longer deadlock. |
| 2026-11-12 | `de4d99f` | Phase 16 release flow: an outdated page reloads itself after a 10-second countdown in the notice instead of after 60 s, and an attended page after 1 minute without input instead of 2; the Production Board refreshes every 5 seconds instead of 15 (owner decisions). |
| 2026-11-12 | `77a2dfb` | Phase 16 release flow: every outdated page reloads itself once idle, not only the Scan Station and Production Board, and the browser checks after a release on the NAS cover it. |
| 2026-11-12 | `a7949e3` | Lowered the minimum length of a new password from 12 to 6 characters (owner decision). |
| 2026-11-10 | `e0e5056` | The go-live step points to the rewritten NAS installation guide (§1 to §10). |
| 2026-11-09 | `398c617` | Rewritten as current state for new readers; pilot replaced by direct go-live (owner decision). |
| 2026-11-09 | `b405c6e` | Recorded the owner's direct go-live decision with no pilot and no release gates. |
| 2026-10-09 | `03bd90c`, `4271f61` | Phase 16 monitoring: structured logs, request ids, status command, host checks and scheduled reconcile. |
| 2026-10-08 | `a330383`, `92727f6` | Phase 16 backups, restore drill and rollback by restore. |
| 2026-10-08 | `1116b07` | Phase 16 database roles and grants. |
| 2026-10-08 | `b820e7a` | Phase 16 release flow, release identity, readiness and write gate. |
| 2026-10-08 | `d13e303`, `d7e86a9`, `25f63bf` | Phase 16 production images and Compose stack. |
| 2026-10-07 to 2026-10-08 | `e1dee40` to `e13574f` | Phase 15 file import of new and existing Work Orders; Phase 15 closed. |
| 2026-10-07 | `3fb66df`, `4313863` | Phase 16 reconciliation command; percent-encoded database URL accepted. |
| 2026-10-06 to 2026-10-07 | `702ab1c` to `eb7171c` | Phase 14 sign-in, enforcement, station devices, corrections, audit trail and User theme; Phase 14 closed. |
| 2026-10-04 to 2026-10-06 | `c12728d` to `7581844` | Phase 13 slices and closure. |
| 2026-10-04 | `80f7925`, `b9785d2`, `9f466fa` | Phase 12 Priority Management, closed on the owner decisions. |
| 2026-09-13 to 2026-09-14 | `1ce5e19` to `8b3b089` | Phase 11 audit, Machine per-PN breakdown and expected-duration monitoring; Phase 11 closed. |
| 2026-09-02 to 2026-09-10 | `5f006a0` to `de88283` | Phase 11 Production Board, Area Board and PN Tracking. |
| 2026-09-03 to 2026-09-04 | `bc07d3a` to `f10d8bd` | Coverage audit gave every target behavior an owner; Phase 10.5 Receive Quantity. |
| 2026-09-01 to 2026-09-02 | `100d59d` to `94f3202` | Phase 10 Stockroom, allocation and completed history. |
| 2026-08-31 to 2026-09-01 | `1d52779` to `7e60dcb` | Phase 9 Undo, Repair, Scrap and quantity additions. |
| 2026-08-28 to 2026-08-31 | `60ac1a6` to `8ee6874` | Phase 8 split and merge. |
| 2026-08-25 to 2026-08-28 | `a567b02` to `485775c` | Phases 5 to 7: transfer, Machine assignment and direct processing. |
| 2026-08-18 to 2026-08-24 | `d6c735f` to `1fe73e9` | Phases 3, 3.5 and 4: domain foundation, environment setup, Work Orders and release. |
| 2026-07-23 to 2026-08-17 | `1a3ebbf` to `ecc1ef4` | Phases 1 and 2 and the GUI design rounds. |
| 2026-07-22 | `483ef0c` | First version of the roadmap. |
