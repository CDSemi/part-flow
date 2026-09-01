# PartFlow

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
history (frontend pending):

- `frontend/` — React + TypeScript (Vite): design tokens with switchable
  Dark/Light themes (Dark default), application shell with routing, the
  real `/api/health` connectivity integration, and the ten approved GUI
  views — the real views (Administration's minimum-environment
  sections and Management → Machines from Phase 3.5, Management →
  Work Orders from Phase 4, and the Scan Station from Phases 5–8) read and
  write the real `/api` surface through the shared client layer in
  `src/api/` and ship in every build from `src/app/real-views.ts`,
  while the remaining views stay development-only mock views until
  their backend slices exist
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
  demand-line removal rule; `/api/part-numbers` — canonical lookup and
  create-or-reuse with the derived `PF:PN:` barcode; the read-only
  `GET /api/route-templates` a `PLANNED` release selects from; and
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
  `POST /api/allocations` (the receiving confirmation or a Management
  allocation, both invariants judged under a per-PN lock plus the
  demand and Work Order row locks, the operator's adjustments flagged
  as overrides, Work Order completion derived and `completed_at`
  projected), `POST /api/allocations/{id}/reversals` (the auditable
  adjustment, once per allocation, reopening a completed Work Order),
  `GET /api/allocations`, and `GET /api/work-orders/completed` (the
  read-only history: search over WO Number / PN / Job Number, done
  range, due outcome, keyset paging); the PN resolution
  and the Area inventory carry each flow's derived processing state,
  Machine and valid actions, the inventory split into queued / per
  Machine card (ON_MACHINE only) / finished; `/api/machines` responses
  carry the derived `operational_state` and `assigned_quantity`),
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
  `device_event_id` + `command_sequence` idempotency pair)
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
production build. The Phase 3.5
configuration surfaces (Administration →
Departments/Areas/Operations/Scan Stations/Barcode configuration and
Management → Machines) read and write real configuration and Machine
master data end to end. Every other view renders development-only mock
data; the Stockroom and
allocation-derived completion (Phase 10), and the remaining tracking
behavior arrive next — the movement-type check admits the
Phase 3–9 types (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`,
`RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`,
`SCRAPPED`, `QUANTITY_ADJUSTED`, `REVERSED`).
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
| `/management/tracking` | Management → Tracking (PN-centric) |
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
  default, session-only), connectivity provider with fast detection
  (browser online/offline events, ~1 s `/api/health` polling with a
  request timeout below the probe interval, recheck on tab
  focus/visibility, explicit Retry; no optimistic writes — a write is
  recorded only after the server confirms it), dev state preview.
- `src/api/` — the shared API client layer of the real views: a thin
  typed fetch core translating the backend's `{"detail": …}` errors
  into user-facing messages, the environment configuration and
  Machines endpoints (Phase 3.5), the Work Orders, Part Numbers,
  route-template and production-release endpoints (Phase 4) and the
  Scan Station context / scan resolution / transfer / Area inventory
  endpoints (Phase 5, `scan-station.ts`) with snake_case ↔ camelCase
  mapping, the ISO 8601 duration helpers, and
  the `useApiData` loading/error/reload hook. Production-safe — never
  imports from `src/mocks/`.
- `src/mocks/` — the development-only mock datasets. The remaining
  mock views read from here and pass data to components via props;
  nothing in `src/mocks` encodes production business rules or is
  written to the backend. Mock views and datasets are reachable only
  through the dev-only registry `src/app/dev-views.ts`
  (`import.meta.env.DEV`), so a production build excludes them
  entirely: the real views (`src/app/real-views.ts` — Administration
  and Machines from Phase 3.5, Work Orders from Phase 4, the Scan
  Station from Phase 5) ship in every
  build against the live `/api` surface, and every other route renders
  an explicit "not connected to a production data source yet" state. `npm run build`
  verifies the boundary by scanning the generated assets for known
  mock sentinel values (`scripts/check-production-boundary.mjs`), and
  `src/production-boundary.test.ts` additionally verifies at the
  source level that no production module imports from `src/mocks/`
  by walking the production module graph transitively from
  `src/main.tsx` (the development-only Worker sessions preview, the
  Completed Work Orders visual preview and the mock Scan Station
  preview of the Phase 6+ workflows — `ScanStationMockView.tsx`,
  `?preview=mock` — stay behind `import.meta.env.DEV`-guarded lazy
  imports, which the walk cuts — an ordinary production dynamic import
  is still followed). Shared view-model types
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

2. Adjust values in `.env` if needed. These are development-only credentials; the real `.env` is git-ignored and must never contain shared or production secrets.

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
Management → Work Orders, Scan Station) plus the remaining
development-only mock views. The top-navigation chip shows the real backend
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
`0006_phase5_transfer`):

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
integration):

- `tests/test_health.py` — health-endpoint **behavior** tests that mock
  `ping_database` (success and safe 503 responses; no database needed).
- `tests/test_part_number_normalization.py` — **unit** tests for the
  canonical Part Number normalization rules (no database needed).
- `tests/test_database_connectivity.py`, `tests/test_phase3_schema.py`,
  `tests/test_phase35_schema.py`, `tests/test_phase4_schema.py`,
  `tests/test_phase5_schema.py`, `tests/test_environment_api.py`,
  `tests/test_machines_api.py`, `tests/test_work_orders_api.py`,
  `tests/test_part_numbers_api.py`, `tests/test_route_templates_api.py`,
  `tests/test_production_release_api.py`, and
  `tests/test_scan_station_transfer_api.py` — **integration** tests that
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
  `0010_phase9_undo_corrections` for Phase 9 — while the Phase 10
  module carries the head-level coverage: the `STOCKED` type and its
  shape branch, the widened flow lifecycle, `work_orders.completed_at`
  and its index, the allocation table's constraints and append-only
  trigger, the downgrade that refuses to drop Phase 10 history, and
  models↔migration parity); and the API tests exercise the endpoints
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
  tie-breaker, the confirmation with overrides and both invariants, the
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
frontend/          Vite + React + TypeScript app (shell + real Phase 3.5/Phase 4/Phase 5 views + mock views)
  src/styles/      semantic design tokens and shared primitives
  src/app/         router, theme, connectivity, real/dev view registries, dev state preview
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
