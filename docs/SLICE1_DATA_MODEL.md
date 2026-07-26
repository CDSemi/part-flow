# Slice 1 — Manual Work Order Intake and Production Release Data Model

> **Status:** Analysis and design only. No migrations or application code.
> **Scope:** Roadmap Phase 4 vertical slice — *manually enter a Work Order and its Work Order Demand, then explicitly release production quantity into the configured starting Area*.
> **Basis:** `docs/PROJECT_PROFILE.md` (v8, canonical — §8, §13, §18, §21 Work Orders, §24–§25, §28), `docs/IMPLEMENTATION_ROADMAP.md` (Phases 3–4), `docs/GUI_DESIGN.md` §12, `CLAUDE.md`.

---

## 1. Slice Goals and Non-Goals

**Goals.** Implement the first business vertical slice end to end:

1. Create or find a WorkOrder.
2. Create or find a PartNumber (with its unique PartFlow barcode).
3. Create or update WorkOrderDemand rows for the Work Order.
4. Save business demand **without** creating production quantity.
5. Provide a separate explicit **production release** command that creates a QuantityFlow, snapshots an AssignedRoute, appends an immutable `RECEIVED` PartMovement, and establishes the current position — transactionally and idempotently.

**Non-goals (explicitly out of this slice).** Scan Station transfer workflows, Machine assignment, Worker/Machine sessions, SPLIT/MERGED, Undo/corrections, Stockroom completion, WorkOrderAllocation, file-based Work Order import, authentication/roles, ERP integration, offline behavior of any kind. See §18.

---

## 2. Required Entities and Responsibilities

| # | Entity | Responsibility in this slice |
|---|--------|------------------------------|
| 1 | `Department` | Organizational owner of Areas; configuration context (initial data: Machine Shop). |
| 2 | `Area` | Stable physical location identity. Provides the configured starting Area for release; destination of `RECEIVED`. |
| 3 | `Operation` | Work supported by an Area. The starting Area's Operation is resolved or confirmed at release and recorded on the `RECEIVED` Movement. |
| 4 | `PartNumber` | Reusable PN master record; unique PN string and unique barcode. The primary tracked identity. |
| 5 | `WorkOrder` | Business order shell: Work Order Number (arbitrary string, never null — generated `TMP-…` when unknown, §5), received date, nullable due date, status. |
| 6 | `WorkOrderDemand` | Requested quantity of one PN for one Work Order: request type, requested quantity, due date, priority, external Job Numbers, requester/reason/notes. Business demand only — never production position. |
| 7 | `RouteTemplate` | Reusable route definition selectable at release. |
| 8 | `RouteStep` | Ordered step of a RouteTemplate: sequence, Area, Operation, expected duration, instructions. |
| 9 | `AssignedRoute` | Independent snapshot of a Route assigned to one QuantityFlow at release; template changes never alter it. |
| 10 | `QuantityFlow` | Traceable portion of physical PN quantity created by release; the unit that will later move. Carries derived current-position projection fields. |
| 11 | `PartMovement` | Immutable event record; this slice produces only `RECEIVED`. The sole source of truth for production state. |
| 12 | Current-position projection | Maintained, rebuildable derived state on `QuantityFlow` (`current_area_id`; `current_machine_id` arrives with Phase 6). |
| 13 | Audit event | Generic append-only audit row for master-data and business-demand changes (§16). Persistence infrastructure, not a domain aggregate. |

Scan Station configuration (`scan_stations`) is stable application/infrastructure configuration, **not** a core domain aggregate (PROJECT_PROFILE §15 Scan Station Persistence). Management-initiated release does not involve a Scan Station, so this slice creates neither the `scan_stations` table nor the `station_id` column; `station_id` remains the canonical column name, added by the Phase 5 migration that introduces `scan_stations`.

---

## 3. Relationships

```
Department   1 ──── *  Area
Area         1 ──── *  Operation
PartNumber   1 ──── *  WorkOrderDemand
WorkOrder 1 ─── *  WorkOrderDemand
PartNumber   1 ──── *  QuantityFlow
RouteTemplate 1 ─── *  RouteStep
QuantityFlow 1 ──── 1  AssignedRoute        (snapshot; copied from a RouteTemplate)
AssignedRoute 1 ─── *  AssignedRouteStep    (snapshot rows)
PartNumber   1 ──── *  PartMovement         (denormalized; must agree with the flow's PN)
QuantityFlow 1 ──── *  PartMovement
Area         1 ──── *  PartMovement (to)    (required; `RECEIVED` has from_area_id NULL)
Operation    1 ──── *  PartMovement         (nullable; recorded when resolved)
```

PN consistency between `part_movements.part_number_id` and the flow's PN is enforced structurally by a composite foreign key `(quantity_flow_id, part_number_id)` referencing `quantity_flows (id, part_number_id)`.

PartMovement carries **no** `work_order_demand_id`. A release may be initiated from a WorkOrderDemand UI context, and that context may be captured informationally in `metadata` for audit display, but Movement remains shop-floor activity at PN + QuantityFlow + quantity level. WorkOrderDemand does not own Movement; WorkOrderAllocation (later slice) remains separate from both.

---

## 4. Business Invariants

1. PN identity is stable and unique: `part_number` and `barcode_value` are unique arbitrary strings, never derived from display names, never reused.
2. Saving or editing WorkOrder/WorkOrderDemand never creates, changes, or destroys production quantity.
3. Production quantity enters the system **only** through an explicit release command that appends a `RECEIVED` Movement. Every QuantityFlow's first Movement is its `RECEIVED`.
4. All quantities are positive integers: WorkOrderDemand requested quantity > 0, flow quantity > 0, Movement quantity > 0.
5. `RECEIVED.quantity` equals the created flow's quantity.
6. A flow occupies exactly one current Area; multi-Area distribution of a PN is represented by multiple flows.
7. Release never merges with existing active quantity and never creates additional quantity implicitly; releasing a PN with active quantity requires explicit UI confirmation of intent (§8).
8. Movement history is immutable and append-only; current state must be reconstructable from it (§15).
9. The QuantityFlow creation (carrying its initial `current_area_id` projection), AssignedRoute snapshot, and Movement insert commit atomically or not at all (§13).
10. A duplicate release submission with the same `device_event_id` and the same normalized request returns the original result and creates nothing; the same `device_event_id` with a different normalized request is an idempotency conflict and creates nothing (§14).
11. Inactive entities (PN, Area, Operation, RouteTemplate) never accept a release.

---

## 5. WorkOrder and WorkOrderDemand Validation

- `work_order_number` is a non-empty arbitrary string, unique among WorkOrders, and **NOT NULL** — the internal Work Order identity is never nullable. When the user confirms saving with a blank Work Order Number, the application generates a unique human-readable temporary internal Work Order Number `TMP-YYYYMMDD-HHMMSS`, extended with a deterministic `-2`, `-3`, … suffix on collision (PROJECT_PROFILE §7 Work Order); the generated value is stored, labeled temporary/internal in the UI, and searchable like any other Work Order Number. Entered Work Order Numbers are stored verbatim, never reformatted. Creating an existing Work Order Number surfaces the existing Work Order (no duplicate) — duplicate handling applies only when a Work Order Number was entered; imports arrive in a later phase and must remain idempotent against the same rule.
- `received_date` is required and defaults to the current date during manual creation.
- `work_orders.due_date` is nullable: a missing Work Order due date is valid data, not a validation error (PROJECT_PROFILE §8.2). It is an entry default for demand-line due dates only.
- Each WorkOrderDemand requires: existing active `part_number_id`, `request_type IN ('NEW','REWORK','MODIFY')` (default `NEW`), and `requested_quantity > 0`. `due_date` is nullable — a missing due date is valid data, never a validation error, and never blocks saving (PROJECT_PROFILE §8.3).
- **Canonical demand ordering key** (PROJECT_PROFILE §18), for every consumer that orders demand: `priority_rank` (Hot rank first), then `due_date` ascending with **NULLS LAST**, then the parent WorkOrder's `received_date` ascending for undated demand, then a stable deterministic tie-breaker (creation order / internal `id`). Slice 1 performs no such ordering itself; supporting indexes arrive with the phases that consume the ordering (allocation, boards, priority — Phases 10–12).
- `job_numbers` stores external Job Numbers as data (list of arbitrary strings) — searchable, displayable, sortable; never a domain aggregate.
- `priority_rank` is nullable; Hot ranking management is a later phase but the column belongs to WorkOrderDemand from the start.
- Editing WorkOrderDemand is permitted (Admin/Manager per PROJECT_PROFILE §8.3); edits are audited (§16) and never touch QuantityFlow or Movement.

---

## 6. PartNumber Creation and Barcode Uniqueness

- Creating a PN captures `part_number` (verbatim arbitrary string) and generates `barcode_value` (`PF:PN:<stable-id>`, PROJECT_PROFILE §10).
- Both carry UNIQUE constraints; the barcode identifies only the PN and encodes no Work Order, quantity, Route, or location context.
- Barcode values are never derived from mutable display names and never reused after deactivation.
- An inactive PN is visible in lookup but flagged; it cannot be added to new demand or released without reactivation.

---

## 7. Separation of Demand Save from Production Release

Two distinct commands with distinct transaction boundaries:

- **Save demand** writes `work_orders` / `work_order_demands` only. No QuantityFlow, no Movement, no projection change. The UI labels demand as "business demand — separate from production".
- **Release to production** (§8) is never triggered implicitly by saving, importing, or editing demand.

This preserves the canonical boundary: WorkOrder and WorkOrderDemand represent business demand and never define current production position; production release explicitly introduces physical quantity.

---

## 8. Explicit Release Command

Input: PN, release quantity, Route (template selection or confirmation of a default), starting Area + Operation confirmation, initiating context (WorkOrderDemand id for audit display only), `device_event_id`.

Steps (one transaction, §13):

1. Validate PN active, Area active and configured as a starting Area, Operation valid for that Area, RouteTemplate active, quantity > 0.
2. If the PN has active flows, require the request to carry the explicit confirmation flag set by the UI after showing the existing distribution; otherwise reject. Never auto-create or auto-merge.
3. Create the QuantityFlow with its initial projection: `current_area_id` = the confirmed starting Area (§9, §15).
4. Snapshot the AssignedRoute (§10).
5. Append the `RECEIVED` PartMovement referencing the snapshot's first step and recording the resolved Operation; its `metadata` carries the request fingerprint (§14) plus, informationally, the initiating actor and optional WorkOrderDemand context (§11).
6. Commit and return: flow id, route snapshot id, starting Area, Operation, quantity, Movement id.

The release transaction appends **no** generic `audit_events` row: the `RECEIVED` Movement is itself the immutable production audit record (§16).

---

## 9. QuantityFlow Creation

- Columns per PROJECT_PROFILE §8.7, restricted to those this slice uses: `id`, `part_number_id`, `quantity`, `status` (`ACTIVE` on creation), `created_at`, `closed_at` (NULL). `parent_flow_id` is the canonical name of the SPLIT lineage column; it is **not** created in this slice and arrives with the Phase 8 migration (§18).
- Plus maintained projection columns: `current_area_id` (NOT NULL; set by the INSERT itself to the confirmed starting Area — a QuantityFlow row never exists without a valid current Area) and `updated_at`. `current_machine_id` is the canonical name of the Machine projection column; it is **not** created in this slice and arrives with the Phase 6 migration (§18).
- Flow quantity is immutable within this slice (no SPLIT/MERGED/QUANTITY_ADJUSTED yet), so conservation is verifiable as Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN).

---

## 10. AssignedRoute Snapshot Creation

- Release copies the selected RouteTemplate's steps into `assigned_routes` + `assigned_route_steps` (sequence, area_id, operation_id, expected_duration, instructions).
- The snapshot references its source template (`source_route_template_id`, informational) but is independent: later template edits never alter it (PROJECT_PROFILE §8.10).
- Exactly one AssignedRoute per QuantityFlow in this slice; route editing and deviations arrive with later phases.
- The snapshot's first step must match the confirmed starting Area (and Operation where the step specifies one); mismatch is a validation failure, not a silent adjustment.

---

## 11. RECEIVED PartMovement

Columns per PROJECT_PROFILE §8.11, with slice-relevant shape:

- `movement_type = 'RECEIVED'` (the only type this slice produces; the enum widens additively later).
- `from_area_id` NULL; `to_area_id` = starting Area (NOT NULL); `operation_id` = resolved starting Operation (NOT NULL — a valid release always resolves or explicitly confirms one, §12).
- `quantity` = flow quantity; composite FK guarantees PN agreement.
- `assigned_route_step_id` → `assigned_route_steps.id`, the **immutable snapshot** step — never the mutable `route_steps` template row, so template edits can never alter the route context recorded by an existing Movement. NOT NULL in this slice: every `RECEIVED` Movement records its snapshot's first step, which §10 requires to match the confirmed starting Area. A future movement type without route context arrives via an additive migration that relaxes this (e.g. widening to a per-type CHECK). The name is deliberately not a generic `route_step_id`, which would be ambiguous between `route_steps` and `assigned_route_steps`; it refines the illustrative `route_step_id` attribute of PROJECT_PROFILE §8.11.
- `station_id`, `worker_id`, `scan_session_id` are canonical column names for later phases (§18); they are **not** created in this slice because their owning tables do not exist yet. Management-initiated release requires none of them.
- `occurred_at`, `server_received_at` — see §14.
- `device_event_id` NOT NULL UNIQUE — idempotency key (§14).
- `metadata` carries the deterministic request fingerprint (§14) and may informationally capture the initiating actor (until authentication exists, Phase 14) and the initiating WorkOrderDemand context for audit display; none of this creates ownership — WorkOrderDemand never owns Movement (§3).
- Immutable: insert-only; UPDATE/DELETE revoked from the application role plus a raise-on-write trigger, per the PostgreSQL-constraints-first rule.

---

## 12. Starting Area and Operation Resolution

- The starting Area comes from Department/Route configuration (PROJECT_PROFILE §22): the Route snapshot's first step, defaulting per configuration (e.g. Material). The release UI confirms it; it is never guessed.
- The Operation is resolved from the starting Area's configuration: a single supported Operation resolves automatically; multiple supported Operations require explicit confirmation in the release flow (ambiguity blocks the write until confirmed).
- Operation therefore exists in the schema **before** the first Operation-bearing Movement — `RECEIVED` records it from day one.

---

## 13. Transaction Boundary

One release submission = one database transaction:

```
BEGIN
  idempotency check on device_event_id + request fingerprint (§14)
  validate PN / Area / Operation / RouteTemplate / quantity
  active-quantity confirmation check
  INSERT quantity_flows            (current_area_id = confirmed starting Area)
  INSERT assigned_routes + assigned_route_steps
  INSERT part_movements (RECEIVED, assigned_route_step_id = snapshot first step)
COMMIT
```

The QuantityFlow is inserted complete, with its initial `current_area_id` projection already set — no intermediate invalid or projection-less row ever exists (`current_area_id` is NOT NULL). Any failure rolls back everything: a Movement is never recorded without its flow and snapshot, and vice versa — no partial release state can commit. No generic `audit_events` row is written here: the `RECEIVED` Movement is the production audit record (§16). PartMovement remains the source of truth and the projection remains rebuildable from it (§15). Demand save is a separate, earlier transaction (§7). No external side effects live inside the transaction.

---

## 14. Idempotency and Retry Behavior

- The client generates one `device_event_id` (UUID) per release submission and reuses it on every transport retry (timeout, connection reset, unknown outcome). `UNIQUE (device_event_id)` guarantees at-most-once recording.
- A duplicate `device_event_id` is safe only when it carries the **same normalized request**. On submission the server computes a deterministic **request fingerprint** — a canonical hash over at least: PartNumber, release quantity, RouteTemplate, starting Area, Operation, and the initiating WorkOrderDemand context when supplied — and stores it in the `RECEIVED` Movement's `metadata` (§11). No separate idempotency table exists: the Movement row, found via `UNIQUE (device_event_id)`, is the idempotency record.
- Replay outcomes:
  - same `device_event_id` + same fingerprint → return the original committed result unchanged; create nothing;
  - same `device_event_id` + different fingerprint → return an explicit idempotency-conflict error; create nothing. A mismatch signals a client defect (an id wrongly reused for a new intent) and is never silently honored.
- A **new** release intent gets a new `device_event_id`; it is then caught by the active-quantity confirmation rule (§8.2), so accidental double release still requires explicit human confirmation.
- Synchronous online semantics: `occurred_at` and `server_received_at` may both be server-assigned and equal. There is **no local offline event queue**; a submission that cannot reach the server fails visibly and is blocked while disconnected (GUI_DESIGN §3.6). `device_event_id` serves request idempotency today and remains compatible with any future, separately approved offline design.

---

## 15. Derived Current-Position Projection

- `quantity_flows.current_area_id` (joined by `current_machine_id` in Phase 6) is a maintained projection — a performance measure for hot read paths (Area inventory, boards). It is set by the QuantityFlow INSERT itself at release (in the same transaction as the `RECEIVED` Movement, §13) and updated inside the Movement transaction by later movement types.
- PartMovement history remains the source of truth: the projection value is defined as the `to_area_id` of the flow's latest Movement, and a replay procedure must be able to rebuild (or assert) every projection from Movement history alone.
- Nothing reads the projection as authority for correctness-critical decisions without holding the flow's row lock inside a transaction.

---

## 16. Audit Persistence Model

Auditable in this slice (PROJECT_PROFILE §28):

- Work Order creation and edits; WorkOrderDemand creation and edits (who, when, what changed).
- PN creation, including barcode issuance.
- Production release — audited by the `RECEIVED` PartMovement itself, the immutable production record; the initiating actor and WorkOrderDemand context live informationally in its `metadata` (§11).

Historical records never disappear; demand edit history must not rewrite prior values silently.

**Persistence.** Two complementary mechanisms, with distinct responsibilities:

1. **PartMovement is the production audit record** — the sole source of truth for production state, replayable into projections (§15), and the sole audit record for production release and all production activity. One production action, one audit record: the release transaction writes **no** duplicate generic audit row (§13).
2. **One generic append-only table, `audit_events`, records master-data and business-demand changes only** — WorkOrder, WorkOrderDemand, and PartNumber. Its rows are descriptive history for display and accountability; they are **never** replayed to build state, and never describe production actions. This is deliberately not an event-sourcing framework.

`audit_events` shape (constraints in §17):

- `id` — BIGSERIAL PK (write order).
- `event_type` — `'CREATED'` or `'UPDATED'` in this slice; widens additively later.
- `entity_type` — `'WorkOrder'`, `'WorkOrderDemand'`, or `'PartNumber'` in this slice.
- `entity_id` — the audited row's PK. Polymorphic by design, so no FK; integrity is guaranteed by writing the audit row in the same transaction as the audited change.
- `actor_reference` — nullable text. Authentication and role enforcement remain deferred (Phase 14); until authenticated users exist, this is NULL or an explicitly configured development/system actor identifier. No user table is invented in this slice; Phase 14 may migrate this to a real user reference.
- `occurred_at` — timestamptz.
- `before_data` / `after_data` — jsonb snapshots of the audited fields (`before_data` NULL for creation events).
- `metadata` — jsonb for contextual detail.

Event mapping in this slice: Work Order create/edit → `CREATED`/`UPDATED` on `WorkOrder`; WorkOrderDemand create/edit → `CREATED`/`UPDATED` on `WorkOrderDemand`; PN creation (including barcode issuance, captured in `after_data`) → `CREATED` on `PartNumber`. Production release maps to **no** `audit_events` row — its audit record is the `RECEIVED` Movement (§13).

Rules:

- Every audit row commits in the **same transaction** as the change it records; an audited write without its audit row (or vice versa) must be impossible.
- Audit rows are append-only: UPDATE/DELETE revoked from the application role plus a raise-on-write trigger, exactly like `part_movements`.
- Edits append a new `UPDATED` row; prior rows are never rewritten.

---

## 17. Database Constraints and Indexes

**`departments`** — PK `id`; `UNIQUE (name)`; `is_active`; `created_at`, `updated_at`.

**`areas`** — PK `id`; `department_id NOT NULL` FK; `name NOT NULL`; `UNIQUE (barcode_value)` where assigned; `is_active`; `created_at`, `updated_at`. `is_terminal`, `machine_assignment_mode`, and `worker_identification_mode` are canonical names only — not created in this slice, added by the migrations of the phases that first use them (§18).

**`operations`** — PK `id`; `area_id NOT NULL` FK; `code NOT NULL`, `UNIQUE (area_id, code)`; `name`; `is_active`; `created_at`, `updated_at`.

**`part_numbers`** — PK `id`; `part_number NOT NULL UNIQUE`; `barcode_value NOT NULL UNIQUE`; `is_active NOT NULL DEFAULT true`; `created_at`, `updated_at`.

**`work_orders`** — PK `id`; `work_order_number NOT NULL UNIQUE` (never null — a blank entry receives a generated `TMP-YYYYMMDD-HHMMSS` number per §5; the UNIQUE constraint backs the deterministic collision suffix); `received_date NOT NULL`; `due_date` nullable (a missing Work Order due date is valid data, §5); `status`; `created_at`, `updated_at`.

**`work_order_demands`** — PK `id`; FKs `work_order_id`, `part_number_id` NOT NULL; `request_type NOT NULL CHECK (request_type IN ('NEW','REWORK','MODIFY'))`; `requested_quantity int NOT NULL CHECK (requested_quantity > 0)`; `allocated_quantity int NOT NULL DEFAULT 0 CHECK (allocated_quantity >= 0)`; `due_date` nullable (a missing due date is valid data per §5 — never required by validation rule or constraint; undated demand orders after dated demand per the canonical demand ordering key, §5); `priority_rank` nullable; `job_numbers text[] NOT NULL DEFAULT '{}'` (arbitrary external strings preserved verbatim; empty list valid; metadata only — no `Job` aggregate, no FK, and no GIN index in this slice because Slice 1 includes no Job Number search); `requester`, `reason`, `notes` nullable; `created_at`, `updated_at`; index `(work_order_id)`, index `(part_number_id)`.

**`route_templates`** — PK `id`; `name NOT NULL`; `version int NOT NULL CHECK (version > 0)` (simple positive integer — no semantic-version parsing, no route-version framework); `UNIQUE (name, version)`; `is_active`; `created_at`, `updated_at`.

**`route_steps`** — PK `id`; `route_template_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (route_template_id, sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable; `expected_duration` nullable; `instructions` nullable.

**`assigned_routes`** — PK `id`; `quantity_flow_id NOT NULL UNIQUE` FK (one snapshot per flow in this slice); `source_route_template_id` FK nullable (informational); `created_at` (snapshot time).

**`assigned_route_steps`** — PK `id`; `assigned_route_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (assigned_route_id, sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable; `expected_duration` nullable; `instructions` nullable (snapshot copies of the template step fields, §10).

**`quantity_flows`** — PK `id`; `part_number_id NOT NULL` FK; `quantity int NOT NULL CHECK (quantity > 0)`; `status NOT NULL DEFAULT 'ACTIVE'`; `current_area_id NOT NULL` FK (set at INSERT, §13); `UNIQUE (id, part_number_id)` (composite-FK target); `created_at`, `updated_at`, `closed_at` nullable; index `(part_number_id) WHERE status = 'ACTIVE'`; index `(current_area_id)`. `parent_flow_id` is not created in this slice (§9, §18).

**`part_movements`** — PK `id BIGSERIAL` (event order); `quantity_flow_id`, `part_number_id` NOT NULL with composite FK `(quantity_flow_id, part_number_id)` → `quantity_flows (id, part_number_id)`; `movement_type NOT NULL CHECK (movement_type IN ('RECEIVED'))` (widens additively); `quantity int NOT NULL CHECK (quantity > 0)`; `from_area_id` FK nullable, `to_area_id NOT NULL` FK; shape check `(movement_type = 'RECEIVED' AND from_area_id IS NULL)`; `operation_id NOT NULL` FK; `assigned_route_step_id NOT NULL` FK → `assigned_route_steps (id)` (both NOT NULL directly while `RECEIVED` is the only movement type — the simplest Slice 1 schema; a later movement type that lacks either relaxes the constraint additively, §11); `occurred_at timestamptz NOT NULL`; `server_received_at timestamptz NOT NULL`; `device_event_id NOT NULL`, `UNIQUE (device_event_id)`; `metadata jsonb`; index `(quantity_flow_id, id)`; immutability guard (revoke UPDATE/DELETE + raise trigger).

**`audit_events`** — PK `id BIGSERIAL`; `event_type NOT NULL CHECK (event_type IN ('CREATED','UPDATED'))` (widens additively); `entity_type NOT NULL CHECK (entity_type IN ('WorkOrder','WorkOrderDemand','PartNumber'))` (widens additively); `entity_id NOT NULL` (no FK — polymorphic, §16); `actor_reference` nullable; `occurred_at timestamptz NOT NULL`; `before_data jsonb` nullable; `after_data jsonb` nullable; `metadata jsonb`; index `(entity_type, entity_id, id)`; append-only guard (revoke UPDATE/DELETE + raise trigger), per §16.

Every table above exists in and is used by this slice; the Slice 1 migration contains **no** foreign keys to tables it does not create. `scan_stations` remains documented Phase 5 configuration (§2) and is created by the Phase 5 migration together with `part_movements.station_id`.

Cross-row invariants PostgreSQL cannot express declaratively (projection agrees with latest Movement; first Movement of a flow is `RECEIVED`; every audited change commits with its audit row) are enforced by the transaction protocol (§13, §16) and verified by replay/reconciliation checks (§15).

---

## 18. Explicitly Deferred Capabilities

| Deferred | Arrives | Additive path |
|---|---|---|
| Scan Station transfer, `TRANSFERRED` | Phase 5 | widen movement-type check; migration adds `scan_stations` + `part_movements.station_id` (canonical name documented, §2) |
| Machine assignment, sessions, `ASSIGNED_TO_MACHINE` / `RELEASED_FROM_MACHINE` | Phase 6 | migration adds `machines` table plus `quantity_flows.current_machine_id` and Movement machine columns (canonical names documented, §9) |
| Area ownership modes beyond queue | Phase 7 | migration adds `areas.machine_assignment_mode`, `areas.worker_identification_mode` (canonical names documented, §17) |
| SPLIT / MERGED, partial movement | Phase 8 | migration adds `quantity_flows.parent_flow_id` (self-reference; canonical name documented, §9); widen type check |
| Undo / corrections, `REVERSED`, `reverses_movement_id` | Phase 9 | append-only model already assumes it; migration adds `reverses_movement_id` |
| Stockroom, `STOCKED`, WorkOrderAllocation | Phase 10 | new tables; migration adds `areas.is_terminal` (canonical name documented, §17); Allocation already separate from Movement by design |
| Monitoring read models | Phase 11 | movement-derived queries |
| Priority / Hot management UI | Phase 12 | `priority_rank` column already present |
| Administration UI | Phase 13 | master-data tables already present |
| Authentication and roles | Phase 14 | no schema coupling to Movement; `audit_events.actor_reference` may migrate to a real user reference (§16) |
| File-based Work Order import | Phase 15 | reuses §5 validation idempotently |
| Worker identification, ScanSession persistence | Phase 6+ | migration adds `worker_id`, `scan_session_id` (canonical names documented, §11) |
| ERP fields/synchronization, offline synchronization | Deferred (unapproved) | isolated integration boundary; `device_event_id` compatible |

---

## 19. Acceptance Criteria

1. Saving a Work Order with WorkOrderDemand creates no QuantityFlow, no PartMovement, and no projection change.
2. Creating a new PN issues a unique barcode; duplicate PN or barcode values are impossible (constraint-verified). The creation appends a `CREATED` audit event recording the issued barcode.
3. A release creates exactly one QuantityFlow, one AssignedRoute snapshot, and one `RECEIVED` Movement — atomically, with no generic audit event. If any part fails, nothing commits: no committed state can contain a QuantityFlow without its `RECEIVED` Movement or without its AssignedRoute snapshot, and no partial release state is ever observable.
4. `current_area_id` is set to the confirmed starting Area by the QuantityFlow INSERT itself; the column is NOT NULL and no post-insert projection update is required for release.
5. Every `RECEIVED` Movement records a resolved Operation and references the AssignedRoute snapshot's first step: `operation_id` and `assigned_route_step_id` are NOT NULL (constraint-verified), and the referenced step is an `assigned_route_steps` row, never a `route_steps` row.
6. Releasing with an invalid or inactive PN, Area, Operation, RouteTemplate, or quantity ≤ 0 is rejected with no write.
7. Releasing a PN that already has active quantity without the explicit confirmation flag is rejected with no write; with confirmation it creates a separate flow and never merges.
8. Retrying a release with the same `device_event_id` and the same normalized request (matching fingerprint) returns the original result and creates nothing.
9. Reusing a `device_event_id` with a different normalized request (mismatched fingerprint) returns an explicit idempotency-conflict error and creates nothing.
10. Editing a RouteTemplate after release does not change any AssignedRoute snapshot, and template edits never alter the route context recorded by any existing Movement.
11. The projection rebuild procedure reproduces `current_area_id` for every flow from Movement history alone.
12. `part_movements` and `audit_events` rows cannot be updated or deleted by the application role (trigger/permission verified by test).
13. Every WorkOrder and WorkOrderDemand creation or edit appends an `audit_events` row (`CREATED`/`UPDATED` with `before_data`/`after_data`) in the same transaction as the change; edits preserve all prior audit rows unchanged (append-only history, verified by test).
14. `audit_events` contains rows only for `WorkOrder`, `WorkOrderDemand`, and `PartNumber` — never for production release or any other production activity (constraint- and test-verified).
15. Conservation holds: Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN).
16. No Slice 1 migration contains a foreign key to any table it does not create, nor any unused deferred column: no `machines`, `workers`, `scan_sessions`, `scan_stations`, or user-table references, and no `current_machine_id`, `parent_flow_id`, `station_id`, `worker_id`, `scan_session_id`, `reverses_movement_id`, `is_terminal`, `machine_assignment_mode`, or `worker_identification_mode`.

---

## 20. Remaining Uncertainty

Only items already tracked in PROJECT_PROFILE §32 touch this slice, and none blocks it:

- **§32.1** (return from stock to active production) and **§32.2** (scrap/reject Movement types) may later widen the movement-type enum — additive.
- **§32.4** (offline scan synchronization) — this slice assumes synchronous online semantics (§14); `device_event_id` was chosen so a future approved offline design would not require renaming.
- The former due-date uncertainty is resolved: PROJECT_PROFILE v8 (§8.2, §8.3) fixes both `work_orders.due_date` and `work_order_demands.due_date` as nullable — a missing due date is valid data, never a validation error — with undated demand ordered after dated demand by the canonical demand ordering key (§5). No policy toggle or migration is needed.
