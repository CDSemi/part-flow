# PartFlow

> **Language:** English is the source of truth. [Tiếng Việt](./README.vi.md).
> See the [documentation index](./docs/README.md) and
> [deployment guide](./docs/DEPLOYMENT.md).

Internal manufacturing tracking system for barcode-driven movement of
production quantities through the factory.

This repository currently contains the **Phase 1 Repository Foundation**,
the **Phase 2 Frontend Design System and Application Shell**, the
**Phase 3 Minimum Canonical Domain and Data Foundation**, the completed
**Phase 3.5 Minimum Environment Setup** (persistence, the configuration
APIs, and the real Administration/Machines frontend), and the completed
**Phase 4 Manual Work Order Intake and Production Release** — the first
business vertical slice, end to end — the completed **Phase 5 Scan
Station Transfer to an Area Queue** (persistence, the transfer command,
the Scan Station read models, and the real Scan Station frontend), and
the completed **Phase 6 One-Shot Machine Assignment and Area
Completion** (persistence, the Machine-Area processing commands, the
Machine-first / PN-first read models, and the real Scan Station
Machine workflows), and the completed **Phase 7 Direct Area Processing
(Areas Without Machines)** — the derived `PROCESSING` state, the
direct-processing DONE without a Machine, the implicit completion on
transfer, and the real Scan Station direct-processing presentation and
`DONE`, and the completed **Phase 8 Quantity SPLIT and MERGED
Workflows** (persistence and lineage, partial quantity on every Scan
Station command, the explicit merge, and the real Scan Station partial
quantity and `Combine quantities` workflows), and the completed **Phase 9
Undo, Corrections, and Auditable Quantity Events** —
command-level Undo with compensating `REVERSED` Movements and a
preview read model, Repair as `TRANSFERRED · movement_reason REPAIR`,
Scrap (`SCRAPPED`) and quantity additions (`QUANTITY_ADJUSTED ·
INCREASE`), and the real Scan Station correction workflows on that
API (`Add more quantity`, `Return quantity for repair`, the PF:SCRAP
counting Scrap workflow, and Undo from the Last Scanned PN block), and
the **Phase 10 Stockroom and WorkOrderAllocation backend** — the
`STOCKED` arrival at a terminal Area (the same command mechanism as
the transfer, the flow closing as manufacturing-complete), the
append-only `work_order_allocations` record with the canonical-order
suggestion, the confirmation and the auditable reversal, and Work
Order completion derived from allocation with the read-only completed
history — and the **Phase 10 frontend**: the Stockroom station's
`Receive into Stockroom` workflow with the receiving allocation dialog
on the Scan Station shell, and the real Completed Work Orders page on
the server-side history — the **Phase 10.5 Scan
Station Receive Quantity Gap Closure** — the three-view `Receive
Quantity` wizard that INTRODUCES a canonical Part Number with no
active Work Order Demand (a Part Number seen for the first time
included) as one transaction: the internal Work Order without an
external number created or reused, its Work Order Demand, the Quantity
Flow, the `PLANNED` route snapshot and the immutable `RECEIVED`
Movement carrying the Scan Station and the resolved Operation, dated
from the scan itself — and
the **Phase 11 Production Board, Area Board and PN Tracking**: the
Department-wide board read model derived from the current-position
projection and the Movement history (`GET /api/production-board`) and
the real large-display board on it (auto-refresh, stale feed, kiosk
mode), the Management Area Board on `GET /api/area-board` —
one read of the Department carrying the SAME Area monitoring model
the Scan Station reads — and the PN-centric Management Tracking on
`GET /api/tracking` (the searchable, filterable PN list) and
`GET /api/tracking/detail` (one PN's demand, current quantity by Area
/ Machine, Quantity Flows with lineage and routes, and its paged
immutable Movement history) — and the **Phase 12 Priority Management**:
the Hot list of Work Order Demands (`GET /api/hot-list`,
`GET /api/hot-list/candidates`, `POST /api/hot-list/changes`) and the
real Management → Priority view on it — and the **Phase 13 Workers registry** (in progress; slices 1, 2, 2b, 2c, 3, 4, 5, 6, 7, 8, 9, 10, 11 and 12): `/api/workers` with its avatar endpoints and the real Administration → Workers section, plus the audit of every Department, Area, Operation, Scan Station and Asset Tag format write (slice 2), plus the Machine configuration audit, two lost-race fixes and the collation-independent PN CHECK (slice 2b), plus the parent-activity locks that serialize child configuration writes with a concurrent parent deactivation (slice 2c), plus the Area Worker ID modes (Disabled / Fixed Worker) and the Worker recorded on production Movements and station allocations (slice 3), plus the scanned Worker Sessions with their sliding inactivity timeout (`GET`/`PUT /api/policies/worker-sessions`, a per-Area override on the Area, badge sign-in through `POST /api/scan-stations/{id}/badge-scans`) and the real Administration → Worker sessions section (slice 4), plus the badge-confirmation gates of `DONE`, `QUEUE` return and Undo (the three options on the same policy, `confirming_badge` on the three commands, the badge Worker signed in by the command) with Scanned session selectable in Administration → Areas (slice 5), plus the Undo reason policy (`GET`/`PUT /api/policies/correction-permissions`, an optional `reason` on the Undo command that is mandatory while the switch is on, the real Administration → Correction permissions switch and the required Reason field in the Scan Station Undo summary) (slice 6), plus Part Numbers management (the real Management → Part Numbers view and the shared `Edit Part Number` dialog on `/api/part-numbers`: create-only creation, a partial edit, the image, the bounded `/page` search and the hard delete of the master record; the pencil `Edit Part Number` control on Work Order demand lines; the saved name on Add Part results, the Production Board rows and PN Tracking) (slice 7), plus Planned Routes management (the real Management → Planned Routes view on `/api/route-templates`: the management list with usage, create, full-replacement edit with an optional advisory preferred Machine per step, archive of an ever-used route, delete of a never-used route, the usage list; the preferred Machine is copied into the Assigned Route snapshot) (slice 8), plus the Department display settings and the Due Soon policy (the Production Board rotation timing per Department through the Department `PATCH`, `GET`/`PUT /api/policies/due-soon`, the real Administration → Department display settings and Administration → Settings sections; every due countdown uses the server policy) (slice 9), plus the Scan Station theme persistence of the station tier (`PUT /api/scan-stations/{id}/theme-preference`, not audited; `GET /api/scan-stations/{id}/context` reports the saved theme; the theme toggle on a Scan Station route saves it for that station while connected) (slice 10), plus the Movement-history retention period (`GET`/`PUT /api/policies/data-retention`, audited; the real Administration → History archival & purge section stores a period of 12–1200 whole months or none and states that archival and purge runs are not available yet — nothing reads the period, and nothing is archived or purged; Administration → Machine assignment is a read-only statement of the two Area modes, and Scan behavior states that it is not available yet) (slice 11), plus Users, roles and permissions configuration (`/api/roles` and `/api/users`, audited; the real Administration → Users and Roles & permissions sections, and the role × correction-permission table in Administration → Correction permissions; the stored User theme preference had no writer until Phase 14 slice 8, and nothing read a user, a role or a permission to allow or refuse an action before Phase 14) (slice 12) — and, in the open **Phase 14**, **sign-in for application Users** (slice 1): local login name and password accounts, server sessions and the CSRF header rule (`/api/session`, `/api/session/password`), the first-run setup of the first Administrator with a one-time setup token printed to the server log (`/api/setup`), `PUT /api/users/{id}/password`, the sign-in settings (`GET`/`PUT /api/policies/sign-in`), the recovery command `python -m app.cli reset-password`, and the account chip, sign-in dialogs, Users `Set password…` and Settings → User sign-in in the frontend; since **slice 2**, **Administration enforcement**: every Administration read needs a signed-in User and every Administration write its permission (the Administration sections are view-only without it), the permission-management guard and last-holder rule, the route registry `app/api/route_access.py`, the recovery command `python -m app.cli restore-correction-permission-management`, and `actor_user_id` on the audit rows these writes append; since **slice 3**, **Management enforcement**: every Management write needs its permission (Work Order saves are judged by their content and Hot list changes by whether they change the list's members or only its order), every Management read needs View all current and historical production data or a permission the view's actions use, `POST /api/allocations` is the Stockroom station's receiving confirmation only (it requires `station_id`) while Management allocates later on `POST /api/allocations/management` and reverses on `POST /api/allocations/{id}/reversals` (both need Edit Work Order Allocation), `actor_user_id` is recorded on the Management rows, the Machine retire and reactivate requests no longer take an `actor`, a Management command replayed by a different User is refused (409), the route registry classifies every route, and the Management screens ask for sign-in and hide the changes a user may not make; since **slice 4**, **Scan Station devices**: every Scan Station route requires the header `X-PartFlow-Station-Device` with a device an administrator enrolled for that station (a one-time enrollment code exchanged for a device token; Administration → Scan Stations → `Devices…`), each station command needs the permission of the role applied at Scan Stations (initially Operator), the Scan Station hides the actions that role does not grant and shows an enrollment screen on an unenrolled device (see [Enrolling Scan Station devices](#enrolling-scan-station-devices)); since **slice 5**, the **Management allocation workflow and the beyond-demand correction**: the shared `Adjust WO Allocation` dialog (Work Order Details `Allocate from stock…` / `Reverse…` on every saved line, including a Completed Work Order, and Tracking's Corrections section) over `POST /api/allocations/corrections` — the authorized allocation of a demand line beyond its remaining demand, with a mandatory reason, never above the available stocked quantity, recorded `exceeds_demand` — and `GET /api/allocations/management/context`, all needing Edit Work Order Allocation; since **slice 6**, the **AssignedRoute adjustment**: Tracking's Corrections section gains `Edit assigned Route…` over `POST /api/quantity-flows/{quantity_flow_id}/route-adjustments` — the authorized, reasoned replacement of only the future steps of one Planned Quantity Flow's own route, audited as `ROUTE_ADJUSTED` with the complete previous and new steps and never a Movement — and `GET /api/tracking/assigned-routes?part_number=` (the route editor's read), both needing Assign and edit Routes; since **slice 7**, the **PN audit trail and Change priority**: Tracking's Corrections section gains `View audit trail` over `GET /api/tracking/audit-trail?part_number=` — a read-only, paged list of the PN's recorded changes (Part Number details, Work Orders and demand lines, Hot rank changes, Management allocation entries and reversals, route adjustments), open to every user who may open Tracking — and `Change priority`, a link into Management → Priority for one PN; since **slice 8**, the **Theme User tier**: `PUT /api/session/theme-preference` saves the signed-in User's own Dark/Light preference (not audited) and the session reports it; the User preference applies above the Scan Station's on every route; and, in the open **Phase 15** (slice 1 implemented), the **file-based Work Order import**: Management → Work Orders gains `Import from file…` over `POST /api/work-orders/import/preview` (`Check file`, a dry run that writes nothing and returns a `check_token`), `POST /api/work-orders/import` (the import of the same bytes, one transaction per Work Order, carrying that token in the `X-PartFlow-Import-Check` header) and the two header-only templates `GET /api/work-orders/import/template.csv` and `GET /api/work-orders/import/template.xlsx`, all needing Create and edit Work Orders — a UTF-8 CSV or the first worksheet of an `.xlsx` (stored values only, columns A–BL), at most 1 MB, 2,000 rows, 500 lines per Work Order and 200 characters per text cell; it creates new Work Orders only and reports an existing Work Order Number as not changed (updating active Work Orders is slice 2); the backend dependencies `openpyxl` (and the development-only `types-openpyxl`) are new — after pulling this change run `docker compose up --build -V` (see [Start the complete stack](#start-the-complete-stack)); the new backend test modules are `tests/test_work_order_import_analysis.py`, `tests/test_work_order_import_files.py` and `tests/test_work_order_import_api.py`; and, in the open **Phase 16**, the read-only **reconciliation command** `python -m app.cli reconcile` (slice 1; see [Reconciliation (read-only)](#reconciliation-read-only)):

- `frontend/` — React + TypeScript (Vite): design tokens with switchable
  Dark/Light themes (Dark default), application shell with routing, the
  real `/api/health` connectivity integration, and the ten approved GUI
  views — the real views (Administration's minimum-environment
  sections and Management → Machines from Phase 3.5, Management →
  Work Orders from Phase 4 with the Completed Work Orders page from
  Phase 10, the Scan Station from Phases 5–10.5, and the Production
  Board, Area Board and PN Tracking from Phase 11, Priority from
  Phase 12, and Part Numbers and Planned Routes from Phase 13) read and
  write the real `/api` surface through the shared client layer in
  `src/api/` and ship in every build from `src/app/real-views.ts`;
  every approved view is real, so no development-only mock view
  remains
- `backend/` — FastAPI with the operational health endpoint
  (`GET /api/health`), the Phase 3.5 environment configuration API
  (Departments, Areas with derived `PF:AREA` barcodes, Operations,
  Scan Stations, and the Machine Asset Tag format under
  `/api/barcode-configuration`), the Phase 3.5 Machines management API
  (`/api/machines` — automatic Asset Tag assignment with derived
  `PF:MACHINE` barcodes, metadata editing, the maintenance override,
  and retirement/reactivation committing atomically with their
  append-only lifecycle events), the Phase 4 Work Order intake and
  production-release APIs (`/api/work-orders` — create/find, list and
  server-side bounded search, the one-transaction demand save and the
  demand-line removal rule; `/api/part-numbers` — canonical lookup, search over the PN and the saved
  name, create-only creation (Phase 13) with the derived `PF:PN:` barcode, plus
  the Phase 13 management routes (`PATCH` / `DELETE ?number=`, `GET` / `PUT` /
  `DELETE /image`, `GET /page`); the read-only
  `GET /api/route-templates` a `PLANNED` release selects from (active
  templates only) and the Phase 13 Planned Routes management routes
  (`GET /api/route-templates/management`, `POST /api/route-templates`,
  `PUT /api/route-templates/{id}`, `POST …/{id}/archive`,
  `DELETE …/{id}`, `GET …/{id}/usage`); and
  `POST /api/work-orders/{id}/demands/{id}/release`, the one command
  that introduces production quantity — transactional and idempotent
  per `device_event_id`), and the Phase 5 Scan Station transfer API
  (`GET /api/scan-stations/{id}/context`,
  `POST /api/scan-stations/{id}/scans/resolve` — PN barcode/manual
  entry resolved into in-Area quantity and explicit transfer
  candidates, `POST /api/scan-stations/{id}/transfers` — the one
  command that moves a Quantity Flow, whole or — since Phase 8 — in
  part (the flow is split first inside the same command), appending the immutable
  `TRANSFERRED` Movement and updating the current-position projection
  in one idempotent transaction, and `GET /api/areas/{id}/inventory`),
  the Phase 6 Machine-Area processing commands
  (`POST /api/scan-stations/{id}/machine-assignments`,
  `…/machine-releases` (QUEUE) and `…/area-completions` (DONE) — each
  one Quantity Flow, whole or — since Phase 8 — in part (the flow is
  split first inside the same command), one idempotent transaction;
  since Phase 7
  `…/area-completions` without `machine_id` is the direct-processing
  DONE of an Area without Machines; a transfer of ON_MACHINE or
  directly processing quantity appends `AREA_COMPLETED` + `TRANSFERRED`
  as one command under one `device_event_id`;
  `POST /api/scan-stations/{id}/merges` (Phase 8) — the explicit merge of
  named Quantity Flows of one PN with one identical production context
  into one resulting flow, ancestry kept, never automatic;
  `POST /api/scan-stations/{id}/machine-scans/resolve` — a
  `PF:MACHINE:` barcode resolved into the one-shot Machine-first
  assignment context with the Area's queued flows;
  the Phase 9 correction commands —
  `POST /api/scan-stations/{id}/scraps` (one auditable `SCRAPPED`
  operation per confirmation, mandatory reason, partial via the same
  in-command SPLIT), `POST /api/scan-stations/{id}/quantity-additions`
  (`QUANTITY_ADJUSTED · INCREASE` introducing a new FLOATING flow
  beside existing in-Area quantity, mandatory reason, requested
  quantities untouched), the transfer's explicit `repair` intent
  (`movement_reason = REPAIR` with a mandatory reason, previously
  visited destinations only), and
  `GET /api/scan-stations/{id}/undo-preview/{device_event_id}` +
  `POST /api/scan-stations/{id}/undos` — the §16 summary confirmation
  and the command-level Undo that reverses one complete committed
  command with compensating `REVERSED` Movements, the originals
  preserved and the projection restored from the reversal-aware
  derivation; the Phase 10 Stockroom and allocation surface —
  `POST /api/scan-stations/{id}/stockings` (the `STOCKED` arrival at a
  station bound to a terminal Area: the transfer's shape minus Repair,
  implicit `AREA_COMPLETED`, partial via the same in-command SPLIT,
  whole-command idempotency; the flow closes as manufacturing-complete
  and is never undoable), `GET /api/allocations/suggestion` (the
  canonical demand ordering — Hot rank, dated earliest first, undated
  by received date — proposing up to each line's remaining shortage
  and never beyond the derived available stocked quantity),
  `POST /api/allocations` (the Stockroom station's receiving confirmation, which requires `station_id`, and `POST /api/allocations/management`, the Management allocation needing Edit Work Order Allocation, each naming the explicit allocation quantity the lines must
  sum to — refused when stale against the derived available stock —,
  both invariants judged under a per-PN lock plus the
  demand and Work Order row locks, the operator's adjustments flagged
  as overrides, Work Order completion derived and `completed_at`
  projected), `POST /api/allocations/{id}/reversals` (the auditable
  adjustment, Management only and needing Edit Work Order Allocation, once per allocation, reopening a completed Work Order),
  `GET /api/allocations`, and `GET /api/work-orders/completed` (the
  read-only history: search over WO Number / PN / Job Number, done
  range (named presets anchored to the site's current date, or explicit
  dates), due outcome and done date on the site calendar
  (`SITE_TIMEZONE`), server-side sort, keyset paging with a cursor only
  while a further row exists, the matching and the whole-history
  totals); the Phase 10.5 receipt —
  `POST /api/scan-stations/{id}/receipts` (`Receive Quantity`: one
  transaction introducing a canonical PN with no active Work Order
  Demand — no demand line with a remaining business shortage — at a
  non-terminal Area, creating the PartNumber
  master on first use, the internal Work Order without an external
  number or reusing the single applicable one by raising its existing
  demand line, the WorkOrderDemand, the QuantityFlow, an AssignedRoute
  snapshot for `PLANNED` only and the immutable `RECEIVED` Movement
  with the Scan Station and the resolved Operation; several plausible
  internal Work Orders are refused with the candidates for an explicit
  selection, every stale context writes nothing, and the receipt is
  never undoable because it also created the demand behind the
  quantity; a receipt beside ACTIVE quantity of the PN creates a
  SEPARATE Quantity Flow and only after the operator's explicit
  confirmation — nothing is ever merged, and `Combine quantities`
  stays the one merge; `received_date` comes from the scan the
  resolution stamped, not from the confirmation, and the whole entry
  condition is serialized by the shared per-Part-Number lock every
  command that can create demand or quantity takes); the Phase 11 monitoring surface — `GET /api/production-board`
  (the Department-wide board: every PN with active quantity in the
  Department's Areas — or stocked quantity with an open demand — with
  its distribution per Area / Machine / External activity, the derived
  state and the fixed entry timestamp of each position, the stocked and
  scrapped quantities, the open demand context with Work Order Number,
  Job Numbers and allocated quantity, the Hot rank, in the canonical
  demand ordering; `department_id` selects the Department, omitted only for a
  single active Department), `GET /api/area-board` (every active Area
  of the Department with the shared Area monitoring model) and the PN
  Tracking reads — `GET /api/tracking` (the PN list with server-side
  search over PN / Work Order Number / Job Number, the Area /
  Operation / Machine / Request Type / Hot / status / due-window
  filters, the canonical order and offset paging), `GET
  /api/tracking/detail?part_number=` (the read-only PN detail with the
  optional master, the open demand figures, the current quantity by
  Area / Machine, the stock and allocation history, the reconciliation
  figures, every Quantity Flow with lineage, PLANNED snapshot or
  FLOATING trace, and the first page of the immutable Movement
  history) and `GET /api/tracking/movements?part_number=&before=`
  (older history pages, keyset on the Movement id; since Phase 14
  slice 6 each flow also carries every route adjustment of its own
  snapshot, and `GET /api/tracking/assigned-routes?part_number=`,
  needing Assign and edit Routes, serves the route editor; since slice 7,
  `GET /api/tracking/audit-trail?part_number=&before_source=&before_id=&limit=`
  serves the PN-scoped, read-only audit trail, newest first); the PN resolution
  and the Area inventory carry each flow's derived processing state,
  Machine and valid actions, the inventory split into queued / per
  Machine card (ON_MACHINE only) / finished; `/api/machines` responses
  carry the derived `operational_state` and `assigned_quantity`), and the
  Phase 12 Hot list API — `GET /api/hot-list` (the ranked Work Order
  Demands, each with the PN's current quantity
  per Area / Machine), `GET /api/hot-list/candidates` (`?search=` over PN /
  Work Order Number / Job Number, or `?barcode=PF:PN:…`; only demand
  eligible to join the list) and `POST /api/hot-list/changes` (the one
  idempotent, audited command that adds, removes, moves, undoes or redoes
  one entry against the order the manager confirmed — with the automatic
  removal of an entry whose line becomes fully allocated, the only writers of
  `priority_rank`; Department-gated: 404 with no active Department, 409
  with several — only the replay of an already committed change still
  answers, with `entries: null`), with the demand-line removal of a Hot
  line allowed only after a typed confirmation,
  all with Application-layer services in
  `app/application/` owning every rule and transaction, the
  framework-independent domain vocabulary (`app/domain/`), and the
  SQLAlchemy mappings of the canonical Phase 3, Phase 3.5, Phase 4,
  Phase 5, Phase 6, Phase 7, Phase 8, Phase 9 and Phase 10 schema
  (`app/infrastructure/models.py`)
- PostgreSQL 16 with Alembic migrations: the canonical Phase 3 domain
  schema (Departments, Areas, Operations, the optional PartNumber
  master, Work Orders and demand, route templates and snapshots,
  QuantityFlows, and the append-only `part_movements` event table
  guarded by a database trigger) plus the Phase 3.5 environment
  configuration (completed Area/Operation configuration fields,
  `scan_stations`, `machines` with immutable auto-assigned Asset Tags,
  the append-only `machine_lifecycle_events` history, and the singleton
  Machine Asset Tag format configuration) and the Phase 4 additions
  (the append-only generic `audit_events` table with its own
  raise-on-write trigger, and the partial expression index that serves
  the released-quantity derivation over `part_movements`) and the
  Phase 5 Movement widening (`TRANSFERRED` admitted by the movement-type
  check, `part_movements.station_id` recording the Scan Station of a
  scan-driven Movement, and the per-type Movement shape check) and the
  Phase 6 Machine assignment widening (`quantity_flows.current_machine_id`,
  the Movement Machine references, `part_movements.command_sequence`
  with `UNIQUE (device_event_id, command_sequence)`, and the
  `ASSIGNED_TO_MACHINE` / `RELEASED_FROM_MACHINE` / `AREA_COMPLETED`
  types with their shape branches), the Phase 7 direct-processing
  widening (an `AREA_COMPLETED` without a Machine), the Phase 8
  quantity lineage (`SPLIT` / `MERGED` types, the Quantity Flow
  lifecycle closure and the append-only `quantity_flow_lineage` edge
  table) and the Phase 9 corrections widening (`SCRAPPED` /
  `QUANTITY_ADJUSTED` / `REVERSED` types with their shape branches,
  `movement_reason`, the mandatory `reason`, the UNIQUE
  `reverses_movement_id`, and the `SCRAPPED` / `REVERSED` flow
  statuses) and the Phase 10 Stockroom and allocation persistence (the
  `STOCKED` type and flow closure, `work_orders.completed_at` with its
  keyset index, and the append-only `work_order_allocations` table —
  allocation and reversal rows, UNIQUE `reverses_allocation_id`, the
  `device_event_id` + `command_sequence` idempotency pair) and the
  Phase 11 read-path index (`0012_phase11_tracking_index` — one
  composite index `(part_number, occurred_at, id)` on `part_movements`
  for PN Tracking's reverse-chronological history read; no column, table
  or constraint) and the Phase 12 Hot list persistence
  (`0013_phase12_priority` — a pre-check that refuses non-dense ranks, the
  positive-rank CHECK, the UNIQUE `priority_rank` and the audit expression
  index for the command's idempotency lookup) and the Phase 13 Workers
  registry (`0014_phase13_workers` — the `workers` table with the badge
  UNIQUE and canonical-form CHECK and the avatar on the row, plus the
  `DELETED` audit event and `Worker` audit entity; the downgrade refuses;
  `0015_phase13_badge_check` re-creates that CHECK under the `"C"`
  collation so it never depends on the OS libc case tables;
  `0016_phase13_environment_audit` widens the audit entity CHECK with the
  environment entities `Department`, `Area`, `Operation`, `ScanStation`
  and `MachineAssetTagConfig`; the downgrade refuses;
  `0017_phase13_machine_audit` adds `Machine` to the audit entity CHECK;
  the downgrade refuses; `0018_phase13_pn_check_collation` re-creates the
  canonical PN CHECKs under `"C"`; the downgrade refuses;
  `0019_phase13_worker_identity` adds the Area Worker ID mode and Fixed
  Worker and the Worker references on `part_movements` and
  `work_order_allocations`; the downgrade refuses;
  `0020_phase13_worker_sessions` adds the `application_policy` singleton,
  the per-Area timeout override, the append-only `worker_sessions` table
  and `part_movements.scan_session_id`; the downgrade refuses;
  `0021_phase13_badge_confirmation` adds the three badge-confirmation
  options to `application_policy`; the downgrade refuses;
  `0022_phase13_undo_reason_policy` adds the Undo reason option to
  `application_policy`; the downgrade refuses;
  `0023_phase13_part_number_master` adds the Part Number details and image
  to `part_numbers`; the downgrade refuses;
  `0024_phase13_planned_routes` adds `preferred_machine_id` to
  `route_steps` and `assigned_route_steps`, the
  `ix_assigned_routes_source_route_template_id` index and the
  `RouteTemplate` audit entity; the downgrade refuses;
  `0025_phase13_display_settings` adds the two Production Board rotation
  settings to `departments` and the three Due Soon policy columns to
  `application_policy`; the downgrade refuses;
  `0026_phase13_station_theme` adds `scan_stations.theme_preference`; the
  downgrade refuses;
  `0027_phase13_retention_period` adds
  `application_policy.retention_period_months`; the downgrade refuses;
  `0028_phase13_users_roles` adds `roles`, `role_permissions` and `users`
  with the three seeded roles and the `User` / `Role` audit entities; the
  downgrade refuses;
  `0029_phase14_sign_in` adds the sign-in policy columns on
  `application_policy`, `user_credentials`, `user_sessions` and
  `actor_user_id` on `audit_events`, `machine_lifecycle_events` and
  `work_order_allocations`; the downgrade refuses;
  `0030_phase14_station_devices` adds `scan_station_devices`,
  `application_policy.scan_station_role_id` and the `ScanStationDevice` audit
  entity; the downgrade refuses, and the upgrade is refused when no role is
  named Operator — rename the role whose permissions Scan Stations should use
  to Operator, run the upgrade, then rename it back;
  `0031_phase14_beyond_demand` adds `work_order_allocations.exceeds_demand`
  and its shape CHECK; the downgrade refuses while a correction row exists;
  `0032_phase14_route_adjusted` widens the audit vocabulary with
  `ROUTE_ADJUSTED` / `AssignedRoute`, adds the UNIQUE partial adjustment
  idempotency index, `ix_part_movements_assigned_route_step_id` and the
  UPDATE-forbid trigger on `assigned_route_steps`; the downgrade refuses
  while an adjustment is recorded)
- Docker Compose development stack with health checks

**Management → Work Orders (Phase 4)**
saves business demand and, as a separate explicit action, releases
production quantity — creating a Quantity Flow and appending an
immutable `RECEIVED` Part Movement in one transaction. Saving demand
never creates production quantity, and a demand may be released in
parts until its remaining quantity is exhausted. The Phase 5 backend
transfer moves one Quantity Flow into the Area an active Scan
Station is bound to — appending the immutable `TRANSFERRED` Movement
and updating the current position in one idempotent transaction, with
explicit source selection, the confirmed destination Area as a
precondition on the station binding, destination Operation resolution
and Planned-Route deviation confirmation with a reason — Area or
Operation (partial quantity splits the flow first since Phase 8); the
Scan Station view records it — scans resolve on the server, success is
reported only after the server confirmed the write, and the Area
inventory refreshes from the server. The completed Phase 6 adds the
Machine-Area processing commands — assign queued quantity to a Machine,
QUEUE it back, DONE at the Machine (`AREA_COMPLETED`, deriving
READY_TO_TRANSFER with the Area kept as location) — and the implicit
completion of ON_MACHINE quantity on transfer (`AREA_COMPLETED` +
`TRANSFERRED` as one command) — and the Scan Station records them:
a Machine scan opens `Assign to Machine` with the Machine preselected,
a queued PN offers Assign, Machine cards carry the distinct DONE and
QUEUE actions, and the inventory shows queued, per-Machine and finished
quantity separately. The completed Phase 7 adds direct Area processing:
an Area without Machines holds arriving quantity as `PROCESSING` (no
queue, no Machine, the Operation recorded), the Scan Station renders
it as `In processing` / `External processing` with the single `DONE`
row action and the PN-first `Complete Area processing` choice — the
same Area Completion wizard without a Machine field, recorded as a
Machine-less `AREA_COMPLETED` — and a transfer of directly processing
quantity completes it implicitly (`AREA_COMPLETED`, then `TRANSFERRED`
as one command). Phase 8 adds partial quantity on
every one of those commands — the source Quantity Flow is split
atomically inside the same command (three `SPLIT` Movements, then the
action), the selected part receives the action, the remainder keeps
the source's state, the closed source leaves the inventory and the
lineage edges keep the ancestry — and the explicit merge of Quantity
Flows of one PN with one identical production context (`MERGED`,
N → 1). The Scan Station records both: every wizard accepts 1..MAX and
shows the remainder before and after the write (the server splits, the
client never does), and the PN action dialog offers `Combine
quantities` for exactly the groups the server reports combinable —
source selection, a result preview, `Confirm combine`, success only
after the server. Phase 9 adds the correction
workflows end to end: command-level Undo (one complete command
reversed through compensating `REVERSED` Movements, the §16 preview
confirmation, projection restored from the reversal-aware
derivation), Repair as the explicit transfer intent, Scrap and
quantity additions — and the Scan Station VIEW records all of them
(`Add more quantity`, `Return quantity for repair`, the PF:SCRAP
counting Scrap workflow, and Undo from the Last Scanned PN block with
the final warning question); Worker identity, badge gates and Area
barcodes stay honest placeholders, and the approved presentation of
the remaining workflows survives as a development-only mock preview
(`?preview=mock` on a Scan Station route) that never enters a
production build. Phase 10 adds the Stockroom: at a station bound
to a terminal Area a PN scan opens `Receive into Stockroom` instead of
the transfer (the server marks the stockable sources, the partial
quantity is split by the server inside the same `STOCKED` command,
`Confirm stocking` is the only write point), and after the server
confirmed the write the allocation dialog shows the server's
suggestion for exactly the stocked quantity in the canonical demand
order — adjustable per line, `Confirm allocation` enabled only when
the allocated total equals the stocked quantity, the confirmation
carrying that quantity as `allocation_quantity`, a refusal keeping
the dialog open with a refreshed suggestion, a lost response retried
under the same `device_event_id`, success only after the server —
while the Completed Work Orders page is the real read-only history
(server-side search, Done range and due-outcome filters, keyset
`Show more` paging, the read-only details with the done date and the
allocated quantities, and no duplicate of a completed number from the
New Work Order lookup). The Phase 3.5
configuration surfaces (Administration →
Departments/Areas/Operations/Scan Stations/Barcode configuration and
Management → Machines) read and write real configuration and Machine
master data end to end. Phase 11 makes the Production Board real: the
Department-wide board reads `GET /api/production-board` (the
distribution per Area / Machine / External activity with the entry
timestamps the dwell times derive from, stocked and scrapped
quantities, the Work Order and Job Number context, the Hot rank, in
the canonical board order; merged quantity read across every lineage
branch), refreshes itself periodically, keeps the
last complete rows with the explicit `Feed stale — reconnecting`
status whenever no complete board is on screen — a failed refresh, an
unhealthy connection, a first load still running or failed — and keeps the approved
presentation — kiosk mode, pagination and rotation (the rotation timing
is configured per Department since Phase 13), automatic display
scaling, the manual navigation. Phase 11 also makes the **Area Board**
real: one read of the Department (`GET /api/area-board`) returns every
active Area with the same Area monitoring model the Scan Station reads
(the scrapped quantity per PN in the Area included, so the station's
row carries the same `{n} scrapped` line) — so the All Areas overview
and the per-Area detail are two presentations of one answer and cannot
drift from the station — plus each Area's Operations and the terminal
Stockroom's stocked lines with their allocation and the PN's open
demand context. The All Areas overview
is PN-centric (one row per Part Number in an Area, its separate
quantities aggregated into that row's portion chips) while the per-Area
detail keeps one row per separately actionable quantity; a monitoring
row's Hot rank, due date and Job Numbers come from the PN's OPEN Work
Order Demands on both surfaces, the Scan Station included — never from
a completed Work Order the quantity happens to descend from, which
stays on the quantity as provenance for the station's action dialogs,
recaps and the audit alone — and finished
quantity keeps the Machine that completed it even after that Machine is
retired. Phase 11 also makes **PN Tracking** real: the PN-centric
Management list reads `GET /api/tracking` (every PN with production
history or an open demand, searched and filtered server-side, in the
canonical demand order, bounded and paged) through the same polling /
stale-feed behaviour as the boards, and the modeless detail overlay
reads `GET /api/tracking/detail` — the open demand with released /
allocated / shortage figures, the current quantity by Area / Machine
through the same derivation the boards use, the stock and allocation
history, the reconciliation `introduced = active + stocked +
scrapped`, every Quantity Flow with its lineage and its PLANNED
snapshot (done / current / future, confirmed deviations) or FLOATING
actual trace (repeated Areas, Repair, the inherited split prefix),
the Scrap history (the `SCRAPPED` events themselves, undone ones
marked), and the immutable Movement history paged in reverse-
chronological `(occurred_at DESC, id DESC)` order with reversed
originals kept visible beside their `REVERSED` rows — Quantity Flows
(newest first on the immutable flow id, status never a paging position)
and allocation entries page too, so nothing is truncated out of reach; the
derived status counts stock only while it is still unallocated
(`Stocked`), an open demand with nothing in production and no
available stock reading `Open`. Phase 12 makes Management → Priority
real: the Hot list reads and writes `/api/hot-list` (confirmation before
every order change, session Undo / Redo, drag-and-drop and Move Up /
Move Down, add by search or `PF:PN:` scan), and Management → Work Orders
removes a Hot demand line only after a typed confirmation. Every approved view is
real (Management → Part Numbers and Management → Planned Routes became real
in Phase 13); Phase 11 also lists the server's per-PN
breakdown of each Machine's assigned quantity in Management →
Machines (`assigned_lines` on `/api/machines`), was audited on
2026-09-13 (IMPLEMENTATION_ROADMAP Phase 11), and closes with the
expected-duration monitoring of PROJECT_PROFILE §17 (the fixed
`expected_by` instant on every monitoring position — the current
Assigned Route Step's snapshot value, else the Operation default —
judged by the shared UI clock; advisory only) — the movement-type
check admits the
Phase 3–10 types (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`,
`RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`,
`SCRAPPED`, `QUANTITY_ADJUSTED`, `REVERSED`, `STOCKED`).
See `docs/IMPLEMENTATION_ROADMAP.md` for phase boundaries,
`docs/PROJECT_PROFILE.md` for the authoritative project specification,
and `docs/GUI_DESIGN.md` (with `docs/mockups/partflow-gui-mockup-v18.html`)
for the approved target UI.

## Phase 2 frontend

Routes (meaningful URLs; browser back/forward works; unknown routes show
an application-level not-found state):

| URL | View |
|---|---|
| `/scan-station` | Scan Station — Station Selector (root `/` redirects here; never auto-redirects to a station) |
| `/scan-station/:stationId` | One Scan Station in standard mode (e.g. `/scan-station/LATHE-ST-01`); unknown or inactive Station IDs show an explicit error |
| `/scan-station/:stationId/production` | The same Scan Station in production mode — the top application navigation is hidden so operators stay on the station (presentation only, not a security boundary) |
| `/production-board` | Production Board (large display, read-only) |
| `/production-board/kiosk` | Production Board in kiosk mode — the top application navigation is hidden and the board renders its own wall-display header (presentation only) |
| `/management/area-board` | Management → Area Board (All Areas overview + per-Area detail) |
| `/management/machines` | Management → Machines (Machine lifecycle and maintenance — permission-based production master data) |
| `/management/tracking` | Management → PN Tracking (PN-centric list + modeless detail overlay) |
| `/management/work-orders` | Management → Work Orders |
| `/management/work-orders/completed` | Management → Work Orders → Completed Work Orders (read-only history) |
| `/management/planned-routes` | Management → Planned Routes (reusable route definitions — permission-based production master data) |
| `/management/part-numbers` | Management → Part Numbers (PartNumber master metadata and barcode labels — permission-based production master data) |
| `/management/priority` | Management → Priority (Hot WO Demand ranking) |
| `/administration` | Administration |

`/management` opens the last-used sub view of the current session
(Area Board on first open). Routing is a small history-based router
(`src/app/router-core.ts` route table and resolution,
`src/app/router-context.ts` Context and `useRouter`,
`src/app/router-provider.tsx` history state and redirects,
`src/app/link.tsx` client-side `Link`) — no routing dependency was added.

Frontend structure:

- `src/styles/` — semantic design tokens (`tokens.css`; `body.dark` /
  `body.light` supply the values) and shared primitives (`global.css`).
  Component CSS consumes semantic tokens only.
- `src/app/` — shell infrastructure: router, theme provider (Dark
  default; the signed-in User's preference on every route, else the
  Scan Station's on station routes, session-only elsewhere), connectivity provider with fast detection
  (browser online/offline events, ~1 s `/api/health` polling with a
  request timeout below the probe interval, recheck on tab
  focus/visibility, explicit Retry; no optimistic writes — a write is
  recorded only after the server confirms it), dev state preview.
- `src/api/` — the shared API client layer of the real views: a thin
  typed fetch core translating the backend's `{"detail": …}` errors
  into user-facing messages, the environment configuration and
  Machines endpoints (Phase 3.5), the Work Orders, Part Numbers,
  route-template and production-release endpoints (Phase 4), the
  Scan Station context / scan resolution / transfer endpoints
  (Phase 5, `scan-station.ts`), the ONE shared Area monitoring model
  both the Scan Station and the Area Board render
  (`area-inventory.ts`) and the two Phase 11 monitoring reads
  (`production-board.ts`, `area-board.ts`), the Phase 12 Hot list (`hot-list.ts`)
  with snake_case ↔ camelCase
  mapping, the ISO 8601 duration helpers, and
  the `useApiData` loading/error/reload hook. Production-safe — never
  imports from `src/mocks/`.
- `src/mocks/` — the development-only mock datasets. Every approved
  view is now real (Management → Planned Routes, the last one, became
  real in Phase 13 slice 8; its mock dataset and the former dev-only
  view registry were removed), so the only reader left is the
  development-only Scan Station preview (`ScanStationMockView.tsx`,
  behind `import.meta.env.DEV`); nothing in
  `src/mocks` encodes production business rules or is written to the
  backend, and a production build excludes the datasets entirely. The
  real views (`src/app/real-views.ts` — Administration
  and Machines from Phase 3.5, Work Orders with the Completed Work
  Orders page from Phases 4 and 10, the Scan Station from Phase 5,
  the Production Board and the Area Board from Phase 11, Priority from
  Phase 12, Part Numbers and Planned Routes from Phase 13)
  ship in every
  build against the live `/api` surface. `npm run build`
  verifies the boundary by scanning the generated assets for known
  mock sentinel values (`scripts/check-production-boundary.mjs`), and
  `src/production-boundary.test.ts` additionally verifies at the
  source level that no production module imports from `src/mocks/`
  by walking the production module graph transitively from
  `src/main.tsx` (the mock Scan Station
  preview of the Phase 6+ workflows — `ScanStationMockView.tsx`,
  `?preview=mock` — stay behind `import.meta.env.DEV`-guarded lazy
  imports, which the walk cuts — as is the development-only demo
  badges module of the Worker sign-in modal,
  `scan-station-dev-badges.tsx`, imported only by
  `scan-station-dev-badges-slot.tsx`, which the sign-in modal and the
  badge-confirmation gate share — an ordinary production dynamic
  import is still followed). Shared view-model types
  live in `src/views/view-models.ts` (types only — production-safe).
- `src/views/<view>/` — one folder per GUI view. `src/views/scan-station/barcode.ts`
  holds the deterministic `PF:` barcode parsing and PN normalization (PN
  barcodes carry the canonical uppercase, whitespace-free PN itself —
  `PF:PN:<part-number>`).
- `src/components/` — genuinely shared pieces (Area dot, Hot/Type chips,
  view-state blocks, accessible mock dialog, quantity keypad, and the
  shared Area/Machine monitoring components used by both the Scan
  Station and the Area Board detail).

### Previewing UI states (development only)

Append `?state=…` to a view URL in a development build to force a
deterministic state (each view implements the preview states that are
meaningful for it):

- `?state=loading` — skeleton loading state
- `?state=empty` — empty state
- `?state=error` — error state
- `?state=long` — long-data set (over-long PNs, many rows) on the
  data-heavy views that define a deterministic long-data fixture; a
  view without one (e.g. Administration, Priority) renders its normal
  sample data

Example: `http://localhost:5173/management/tracking?state=long`. The
override is gated by `import.meta.env.DEV` and does not exist in a
production build. The disconnected state is real: stop the backend (or
let the health check fail) and the shell shows the persistent OFFLINE
banner with a Retry action while production-write mock controls disable.

## Prerequisites

- Docker with Docker Compose v2 (`docker compose`)
- For development outside Docker (optional): Node.js 24+, Python 3.12+, [uv](https://docs.astral.sh/uv/)

## Environment setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Adjust values in `.env` if needed. These are development-only credentials; the real `.env` is git-ignored and must never contain shared or production secrets. `SITE_TIMEZONE` (an IANA zone name, `UTC` by default) is the factory's calendar: the backend turns a completion timestamp into its done date in this zone for the completed history's Done range, due outcome and displayed date alike — set it to the site's zone (e.g. `America/Los_Angeles`) so "on time" and "late" follow the factory's day, never a browser's.

Dependency lockfiles (`backend/uv.lock`, `frontend/package-lock.json`)
are committed; the Docker builds install strictly from them
(`uv sync --frozen`, `npm ci`). No extra bootstrap step is required on a
clean checkout.

## Start the complete stack

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Health endpoint: <http://localhost:8000/api/health> (also proxied at <http://localhost:5173/api/health>)

The frontend serves the application shell with the real Phase 3.5,
Phase 4 and Phase 5 views (Administration, Management → Machines,
Management → Work Orders, Scan Station) and the later real views; every
approved view is real. The top-navigation chip shows the real backend
connectivity state: CONNECTING…, ONLINE, or OFFLINE with a persistent
banner and Retry action.

Stop the stack with `Ctrl+C`, then:

```bash
docker compose down
```

(`docker compose down -v` additionally deletes the PostgreSQL data volume — normally not needed.)

After changing backend or frontend dependencies
(`pyproject.toml`/`uv.lock`, `package.json`/`package-lock.json`),
rebuild and renew the anonymous dependency volumes so stale
`.venv`/`node_modules` contents do not shadow the rebuilt images:

```bash
docker compose up --build -V
```

## Database migrations (Alembic)

Apply migrations (the Phase 3 canonical domain schema, the Phase 3.5
environment setup, and the Phase 4 slice — `0004_phase4_audit` adding
the append-only `audit_events` table and `0005_phase4_release_index`
adding the partial expression index that serves the released-quantity
derivation — and the Phase 5 revision `0006_phase5_transfer` widening
`part_movements` (`TRANSFERRED`, `station_id`, per-type shape check) on
top of the no-op repository-foundation baseline; the current head is
`0032_phase14_route_adjusted`):

```bash
docker compose exec backend uv run alembic upgrade head
```

### Creating, resetting, and inspecting the development database

The `postgres` image applies `POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` from `.env` **only once — when the data volume is first
initialized**. Changing `.env` later does not change the roles inside an
existing volume, so a mismatched `psql -U …` or IDE data source fails
with `FATAL: role "…" does not exist`. Always connect with the user your
`.env` actually declares, and reset the volume when credentials changed:

```bash
# connect with the user from YOUR .env (defaults are in .env.example)
docker compose exec db psql -U <POSTGRES_USER> -d partflow

# create/update the development schema
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head

# verify the schema
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\dt"
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\d part_movements"

# full reset — DESTRUCTIVE: deletes the postgres_data volume and all
# development data (everything is recreated by `alembic upgrade head`)
docker compose down -v
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head
```

Two more caveats:

- Re-applying a migration that was edited **before it was ever
  committed/shared** requires `alembic downgrade base` +
  `alembic upgrade head` (or the full reset above). Run that only
  against the disposable development database. A migration that has
  been committed or shared is never edited in place — write a new
  revision instead.
- A `$` inside `POSTGRES_PASSWORD` can collide with Docker Compose
  variable interpolation in `.env`; escape it as `$$` if Compose warns
  about an unset variable.

## First-run setup and account recovery

A new database has no Administrator. While none exists, the backend writes a
one-time setup token to its log when it starts, and the **Set up PartFlow**
button in the top navigation opens the first-run dialog:

```bash
docker compose logs backend | grep "Setup token"
```

Open PartFlow, choose **Set up PartFlow**, paste the token and create the first
Administrator. The first creation closes setup for good and the token is then
discarded; a restart of the backend announces a new token only while no
Administrator exists. The token is the only protection of first-run setup, so
complete it before the service is reachable by anyone else and restrict access
to the log until then. Each backend process announces its own token.

**Upgrading to Phase 14 slice 2.** Before and after deploying it, run this in
the database shell (`docker compose exec db psql -U <POSTGRES_USER> -d partflow`)
to count the active users with a password whose role holds each
permission-management key:

```sql
SELECT rp.permission, count(*) AS holders
FROM users u
JOIN user_credentials c ON c.user_id = u.id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE u.is_active
  AND rp.permission IN ('MANAGE_USERS_AND_ROLES', 'MANAGE_CORRECTION_PERMISSIONS')
GROUP BY rp.permission;
```

A missing row means no holder. Both counts should be at least 1, or
`MANAGE_USERS_AND_ROLES` should be absent (first-run setup is then open). If
`MANAGE_CORRECTION_PERMISSIONS` has no holder while `MANAGE_USERS_AND_ROLES`
has, nobody may grant it any more once slice 2 runs: before deploying, grant it
in Administration; after deploying, the backend logs a startup warning and you
restore it from the host:

```bash
docker compose exec backend uv run python -m app.cli restore-correction-permission-management --role-name <role>
```

The command works only while no active user with a password may manage
correction permissions, and the named role must have such a member. It grants
the permission to that role, appends an audit row and changes nothing else.
Each refusal (a holder already exists, no such role, no active user with a
password in the role) writes nothing and exits 1.

If the only Administrator forgets the password, reset it from the host:

```bash
docker compose exec backend uv run python -m app.cli reset-password --login-name <name>
```

The new password is typed at the prompt, never on the command line. The command
works only for a user that already has a password and only while an
Administrator exists; it never creates an Administrator. It sets a temporary
password, ends the user's sign-ins and clears a lock. If the database connection
fails while the reset is being saved, the command says the outcome is unknown
(exit code 2); run it again, because it sets the password again either way.

The backend reads `SESSION_COOKIE_SECURE` (default `false`, because the
development stack runs over plain HTTP). Set it to `true` in the backend
environment when PartFlow is served over HTTPS so the session cookie is sent
only on secure connections; the development `compose.yaml` does not pass it.

## Enrolling Scan Station devices

Since Phase 14 slice 4 a Scan Station works only from a browser an
administrator has enrolled for that station; development stations are enrolled
the same way. Sign in with a user who may manage Scan Stations, open
Administration → Scan Stations → `Devices…` for the station and choose
`Enroll device…`. Enter the one-time code (`XXXXX-XXXXX`, valid 15 minutes,
single use, shown once) on the station within that time: the station browser
exchanges it for a device token it keeps in its browser storage and sends in
`X-PartFlow-Station-Device` with every request. A browser whose storage is
cleared must be enrolled again; `Re-enroll…` replaces a device when its new code
is used, and `Revoke…` stops a lost device at once. While the role applied at
Scan Stations holds a correction permission (initially Undo recent eligible
scans), enrolling also needs the permission to manage correction permissions.
What an enrolled station may do follows the permissions of the role marked
**Applied at Scan Stations** in Roles & permissions (initially Operator). Device
tokens and enrollment codes are bearer credentials: serve PartFlow over HTTPS
and keep the header out of reverse-proxy logs
([deployment guide](./docs/DEPLOYMENT.md)).

## Reconciliation (read-only)

`python -m app.cli reconcile` checks that the stored production state agrees
with Movement history, allocations and the canonical identity rules. It runs in
one read-only database snapshot, prints one JSON report on stdout and never
changes data or repairs a finding.

```bash
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

Options: `--check ID` (repeatable, `a` to `j`), `--statement-timeout SECONDS`
(1-3600, default 300) and `--max-findings N` (1-10000, default 100). Exit codes:
`0` clean, `1` mismatch, `2` could not run. The exit status counts only when the
report is one complete JSON document whose `exit_code` equals it; an empty or
unparseable report means it could not run. Checks (g) and (h) report
`not_applicable` until Movement-history archival and database-role hardening
exist. Do not run a migration while it runs. The checks, the staging form and
the operating rules are in
[`docs/deployment/OPERATIONS_RUNBOOK.md`](./docs/deployment/OPERATIONS_RUNBOOK.md)
§7.

## IntelliJ IDEA / PyCharm setup (database tools and SQL inspections)

Optional, but recommended when working in a JetBrains IDE (verified
with IntelliJ IDEA 2026.2.1). Without these settings the IDE reports
misleading SQL "errors" in the Alembic migrations and database tests
and may block commits on them.

1. **Data source.** Database tool window → `+` → Data Source →
   PostgreSQL: host `localhost`, port `5432`, database `partflow`, user
   and password **from your `.env`** (see the credential caveat above).
   In the schema selection, introspect `public` **and `pg_catalog`** —
   the schema tests query `pg_proc` to verify the append-only trigger
   function, and `pg_catalog` is not introspected by default.
2. **SQL dialect.** Settings → Languages & Frameworks → SQL Dialects →
   set the Project SQL Dialect to **PostgreSQL**, so SQL embedded in
   Python strings is parsed with the right syntax.
3. **SQL resolution scope.** Settings → Languages & Frameworks → SQL
   Resolution Scopes → map the project (or the `backend` directory) to
   your data source's `partflow.public` schema, so table/function names
   in embedded SQL resolve against the real development database.
4. **Refresh after migrating.** The IDE resolves names against its last
   introspection snapshot — refresh the data source (Ctrl+F5) after
   every `alembic upgrade`/reset, or new tables stay "unresolved".

Expected residual warnings that are safe to ignore:

- In the migration's `CREATE TRIGGER` string, the IDE may still report
  the trigger function or `part_movements` as unresolved: each embedded
  SQL fragment is analyzed independently, and those objects are created
  by this very migration (partly via Python `op.create_table`, which
  the SQL resolver cannot see).
- pytest test classes legitimately have no `__init__`; the "Class has
  no `__init__` method" inspection is noise for this codebase.

These are IDE code-analysis findings, not project quality gates — the
canonical gates are the Docker commands under "Quality commands". If
the commit dialog's "Analyze code" check blocks a commit on them, use
"Commit Anyway" or disable that check (Settings → Version Control →
Commit).

## Quality commands

The Linux containers are the canonical environment for all quality
gates. Run these with the Compose stack up (`docker compose up -d`).

### Backend

```bash
docker compose exec backend uv run ruff format --check .   # formatting check (ruff format . to fix)
docker compose exec backend uv run ruff check .            # lint
docker compose exec backend uv run mypy app tests          # type check (strict)
docker compose exec backend uv run pytest                  # tests (see below)
docker compose exec backend uv run alembic upgrade head    # migrations
```

The pytest suite contains three kinds of tests (behavior, unit, and
integration). `tests/test_route_access.py` is a unit test of the route
registry in `app/api/route_access.py`: it fails on any route the registry does
not classify, so a new route must be added there. Tests call signed-in or
permission routes through `client_as` / `admin_of` from `tests/auth_harness.py`,
not through an anonymous client. Calls to Scan Station routes go through
`station_device_client` (an enrolled device for the addressed station), and a new
station route must declare `RequireStationDevice` and be classified in
`route_access.py`:

- `tests/test_health.py` — health-endpoint **behavior** tests that mock
  `ping_database` (success and safe 503 responses; no database needed).
- `tests/test_part_number_normalization.py` — **unit** tests for the
  canonical Part Number normalization rules (no database needed).
- `tests/test_password_hashing.py` and `tests/test_password_policy.py` —
  **unit** tests for the scrypt password hashing and the password policy.
- `tests/test_station_device_domain.py` — **unit** tests for the enrollment
  code and device name rules (no database needed).
- `tests/test_worker_badge_normalization.py` — **unit** tests for the
  canonical Worker badge rule (trim, uppercase, `PF:` and length
  refusals; no database needed).
- `tests/test_database_connectivity.py`, `tests/test_phase3_schema.py`,
  `tests/test_phase35_schema.py`, `tests/test_phase4_schema.py`,
  `tests/test_phase5_schema.py`, `tests/test_environment_api.py`,
  `tests/test_machines_api.py`, `tests/test_work_orders_api.py`,
  `tests/test_part_numbers_api.py`, `tests/test_route_templates_api.py`,
  `tests/test_route_template_management_api.py`,
  `tests/test_production_release_api.py`,
  `tests/test_scan_station_transfer_api.py`,
  `tests/test_workers_api.py`,
  `tests/test_environment_audit_api.py`,
  `tests/test_environment_parent_activity_api.py`,
  `tests/test_machine_audit_api.py`,
  `tests/test_worker_identity_api.py`,
  `tests/test_worker_sessions_api.py`,
  `tests/test_badge_confirmation_api.py`,
  `tests/test_undo_reason_policy_api.py`,
  `tests/test_part_number_management_api.py`, and
  `tests/test_display_settings_api.py`,
  `tests/test_station_theme_api.py`,
  `tests/test_session_theme_api.py`,
  `tests/test_retention_policy_api.py`,
  `tests/test_users_roles_api.py`,
  `tests/test_authentication_api.py`,
  `tests/test_first_run_setup_api.py`,
  `tests/test_route_authorization_api.py`,
  `tests/test_management_authorization_api.py`,
  `tests/test_permission_management_api.py`,
  `tests/test_station_devices_api.py`,
  `tests/test_allocation_corrections_api.py`,
  `tests/test_route_adjustments_api.py`,
  `tests/test_assigned_route_rules.py`,
  `tests/test_audit_trail_rules.py`,
  `tests/test_audit_trail_api.py`, and
  `tests/test_cli.py`,
  `tests/test_reconciliation.py` — **integration** tests (the latter on
  template-cloned databases) that
  require the PostgreSQL service to be
  reachable via `DATABASE_URL`: the connectivity test calls
  `GET /api/health` through the real application wiring with no
  mocking; the schema tests run the real Alembic migrations
  (upgrade → downgrade → upgrade; each phase module stops at its own
  boundary revision — `0002_phase3_domain` for Phase 3,
  `0003_phase35_environment` for Phase 3.5, `0005_phase4_release_index`
  for Phase 4, `0006_phase5_transfer` for Phase 5,
  `0007_phase6_machine_assignment` for Phase 6,
  `0008_phase7_direct_processing` for Phase 7,
  `0009_phase8_split_merge` for Phase 8,
  `0010_phase9_undo_corrections` for Phase 9,
  `0011_phase10_stock_allocation` for Phase 10,
  `0012_phase11_tracking_index` for Phase 11 and
  `0013_phase12_priority` for Phase 12 — while
  `tests/test_phase13_schema.py` carries the head-level coverage
  (`0014_phase13_workers`, `0015_phase13_badge_check`,
  `0016_phase13_environment_audit`, `0017_phase13_machine_audit`,
  `0018_phase13_pn_check_collation`, `0019_phase13_worker_identity`,
  `0020_phase13_worker_sessions`,
  `0021_phase13_badge_confirmation`,
  `0022_phase13_undo_reason_policy`,
  `0023_phase13_part_number_master`,
  `0024_phase13_planned_routes`,
  `0025_phase13_display_settings`,
  `0026_phase13_station_theme`,
  `0027_phase13_retention_period` and
  `0028_phase13_users_roles`, while `tests/test_phase14_schema.py`
  carries the Phase 14 revisions `0029_phase14_sign_in`,
  `0030_phase14_station_devices`, `0031_phase14_beyond_demand` and
  `0032_phase14_route_adjusted` (the single head): the `workers`
  table's constraints, the widened audit vocabulary including the
  environment audit entities, the refusing
  downgrades, and
  models↔migration parity; the Phase 10 module keeps the `STOCKED` type
  and its shape branch, the widened flow lifecycle,
  `work_orders.completed_at` and its index, the allocation table's
  constraints and append-only trigger, and the downgrade that refuses
  to drop Phase 10 history); and the API tests exercise the endpoints
  end-to-end — Phase 3.5 configuration and Machine management (Asset
  Tag allocation, maintenance, retirement and reactivation with their
  atomic lifecycle events) and Phase 4 intake and release (one-save
  one-transaction demand saves with their audit rows, the
  server-bounded active list and unbounded exact number resolution,
  concurrent same-PN adds to one Work Order, the atomic and idempotent
  release command, partial and repeated release with its hard
  remaining cap, terminal-Area rejection, the restricted edit of a
  released line, demand removal, Movement immutability, projection
  replay and conservation) and the Phase 5 transfer (station context
  and Area inventory, PN barcode/manual resolution, source candidates
  by position and route with several sources returned unpicked and
  uncombined, the exact `TRANSFERRED` shape with the matched snapshot
  step, Area and Operation route-deviation refusal until confirmed
  with a reason, the confirmed destination as a station-binding
  precondition, destination Operation resolution, partial-quantity
  and invalid-input rejection with zero writes, idempotent replay —
  independent of later station changes — and conflict, concurrent
  transfers of one flow, and transfer versus Area deactivation,
  station rebind and Operation deactivation) and the Phase 6
  Machine-Area processing (derived QUEUED / ON_MACHINE /
  READY_TO_TRANSFER states, assign / QUEUE / DONE with their exact
  Movement shapes and Machine state derivation, the implicit
  `AREA_COMPLETED` + `TRANSFERRED` command and its whole-command
  replay, refusals with zero writes, cross-kind idempotency conflicts,
  the assigned-quantity retirement blocker, the assign-versus-assign,
  assign-versus-retirement and DONE-versus-transfer races, Machine
  barcode resolution into the one-shot Machine-first context, PN-first
  actions and selection ambiguity, and the queued / on-Machine /
  finished inventory split reconciling with the Machines read model)
  and the Phase 7 direct processing (the derived PROCESSING state on
  release and transfer into an Area without Machines, the explicit
  Operation choice, the Machine-less DONE with every refusal, the
  direct-versus-Machine DONE idempotency conflicts, the implicit
  `AREA_COMPLETED` + `TRANSFERRED` command and its database-level
  atomicity, DONE-versus-transfer and DONE-versus-DONE races, the
  projection replay, the Area mode following its active Machines, and
  the Machine-Area regressions) and the Phase 8 quantity lineage
  (partial Assign / QUEUE / DONE / direct DONE / Transfer from every
  source state with conservation, lineage and the untouched demand,
  the full-quantity regression, PLANNED snapshot copies and the child's
  recorded step or deviation, the explicit merge with every
  incompatibility refused, whole-command replay and conflicting reuse,
  concurrent and stale commands, and the projection replay across a
  lineage tree) and the Phase 9 corrections and Undo (every command
  kind reversed as a whole — including the implicit-completion
  transfer, SPLIT-prefixed partials, merges, Scrap and additions —
  with conservation, Machine totals and route-position restoration,
  consecutive undos walking back, every eligibility refusal with zero
  writes, the preview verdicts, whole-command replay versus mismatched
  reuse, the threaded double-undo race stopped by the database UNIQUE
  backstop, Repair full/partial/unvisited/reason rules and the
  deviation interplay, Scrap full/partial/ON_MACHINE with refusals,
  additions with the Area-mode arrival state, the witness-locked
  in-Area precondition and the station-lock re-check, and the
  `introduced = active + scrapped` reconciliation) and the Phase 10
  Stockroom and allocation (the `STOCKED` arrival from direct
  processing and from a Machine, partial stocking through SPLIT, a
  Planned Route ending at the Stockroom, every refusal with zero
  writes, replay / mismatch / cross-kind reuse, the one-winner race,
  the not-undoable stocked command, the `introduced = active + stocked
  + scrapped` reconciliation, the canonical suggestion ordering and
  tie-breaker with the received date ordering undated demand only,
  the confirmation with the explicit allocation quantity, the stale
  available-stock refusal, overrides and both invariants, the
  paused two-station race stopped by the per-PN lock, the allocation
  versus in-flight stocking interleaving, the reversal reopening a
  completed Work Order and the threaded double-reversal race, the
  read-only completed history with its edit / removal / release
  refusals and the allocated-quantity floor, the history endpoint's
  search, due outcome and keyset paging, and the projection replays of
  `allocated_quantity` and `completed_at`)
  — all
  against dedicated temporary
  databases (`partflow_test_*`), so the configured database role must
  be allowed to create databases (the Compose and CI `partflow_user`
  is).

### Frontend

```bash
docker compose exec frontend npm run format        # format with Prettier
docker compose exec frontend npm run format:check  # formatting check
docker compose exec frontend npm run lint          # ESLint
docker compose exec frontend npm run typecheck     # TypeScript (strict, no emit)
docker compose exec frontend npm run test          # Vitest + React Testing Library
docker compose exec frontend npm run build         # type check + production build + mock-boundary check
```

### Running directly on the host (optional, best effort)

The same commands can be run without the `docker compose exec …` prefix
from `backend/` (with uv) or `frontend/` (with Node 24), but the host is
not the canonical environment: toolchain versions and OS behavior may
differ from the Linux containers used by Docker and CI. Backend notes
for host runs:

- `DATABASE_URL` must be resolvable. `backend/tests/conftest.py`
  defaults it to the development database from `.env.example`; anything
  outside pytest (e.g. `uv run alembic upgrade head`) needs the variable
  set explicitly or a `backend/.env` file.
- The pytest integration tests need the Compose `db` service running
  (its port 5432 is published to the host): the connectivity test
  performs a real database check through `GET /api/health`, and the
  schema tests create and drop temporary `partflow_test_phase3*`
  databases with the configured role. If your
  `.env` uses custom credentials, set `DATABASE_URL` to match before
  running pytest on the host.

When host results disagree with container results, the container
results win.

## Docker development notes

When using the Docker development environment, treat the Linux
containers as the canonical development environment.

### Frontend formatting

Run Prettier **inside the frontend container** before checking
formatting:

```bash
docker compose exec frontend npm run format
```

Then verify:

```bash
docker compose exec frontend npm run format:check
```

Running Prettier directly on the host machine may produce different
results from the Linux container used by Docker and GitHub Actions.

### Complete frontend quality gate

```bash
docker compose exec frontend sh -lc "npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build"
```

### Complete backend quality gate

```bash
docker compose exec backend sh -lc "uv run ruff format --check . && uv run ruff check . && uv run mypy app tests && uv run pytest"
```

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs the same quality gates
on every push to `main` and every pull request: backend format check,
lint, mypy, Alembic migration, and pytest (mocked behavior tests plus
the real PostgreSQL integration test) against PostgreSQL 16; frontend
format check, lint, typecheck, tests, and production build. A separate
`docker` job verifies that the Docker Compose development images build
(`docker compose build`).

## Repository layout

```text
frontend/          Vite + React + TypeScript app (shell + real views for every approved GUI view)
  src/styles/      semantic design tokens and shared primitives
  src/app/         router, theme, connectivity, real view registry, dev state preview
  src/api/         typed API client layer of the real views (production-safe)
  src/mocks/       development-only mock datasets (excluded from production builds)
  src/views/       one folder per approved GUI view
  src/components/  shared presentation components
backend/
  app/api/         HTTP routes (health, environment configuration, Machines management, Work Order intake, Part Numbers, route templates, production release, and the Scan Station transfer surface)
  app/application/ application services (environment, Machines, Work Order intake, Part Numbers, the production release command, the Scan Station read models and the transfer command — every rule and transaction)
  app/core/        configuration (pydantic-settings)
  app/domain/      framework-independent domain vocabulary (PN normalization, enums)
  app/infrastructure/  database engine, connectivity check, and canonical schema mappings
  tests/           pytest suite
  alembic/         migration environment and revisions (baseline + Phase 3 domain schema + Phase 3.5 environment setup + Phase 4 audit table and release-context index + Phase 5 Movement widening + Phase 6 Machine assignment widening + Phase 7 direct-processing completion widening + Phase 8 quantity lineage + Phase 9 corrections widening + Phase 10 Stockroom and allocation)
compose.yaml       development stack (db, backend, frontend)
docs/              canonical project documentation
```
