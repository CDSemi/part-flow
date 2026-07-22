# Slice 1 — Manual PO Intake and Production Release Data Model

> **Status:** Analysis and design only. No migrations or application code.
> **Scope:** Roadmap Phase 4 vertical slice — *manually enter a Purchase Order and its PO Demand, then explicitly release production quantity into the configured starting Area*.
> **Basis:** `docs/PROJECT_PROFILE.md` (v5, canonical — §8, §12, §20 PO Intake, §24, §27), `docs/IMPLEMENTATION_ROADMAP.md` (Phases 3–4), `docs/GUI_DESIGN.md` §12, `CLAUDE.md`.

---

## 1. Slice Goals and Non-Goals

**Goals.** Implement the first business vertical slice end to end:

1. Create or find a PurchaseOrder.
2. Create or find a PartNumber (with its unique PartFlow barcode).
3. Create or update PoDemand rows for the PO.
4. Save business demand **without** creating production quantity.
5. Provide a separate explicit **production release** command that creates a QuantityFlow, snapshots an AssignedRoute, appends an immutable `RECEIVED` PartMovement, and establishes the current position — transactionally and idempotently.

**Non-goals (explicitly out of this slice).** Scan Station transfer workflows, Machine assignment, Worker/Machine sessions, SPLIT/MERGED, Undo/corrections, Stockroom completion, PoAllocation, file-based PO import, authentication/roles, ERP integration, offline behavior of any kind. See §18.

---

## 2. Required Entities and Responsibilities

| # | Entity | Responsibility in this slice |
|---|--------|------------------------------|
| 1 | `Department` | Organizational owner of Areas; configuration context (initial data: Machine Shop). |
| 2 | `Area` | Stable physical location identity. Provides the configured starting Area for release; destination of `RECEIVED`. |
| 3 | `Operation` | Work supported by an Area. The starting Area's Operation is resolved or confirmed at release and recorded on the `RECEIVED` Movement. |
| 4 | `PartNumber` | Reusable PN master record; unique PN string and unique barcode. The primary tracked identity. |
| 5 | `PurchaseOrder` | Business order shell: PO Number (arbitrary string), received date, status. |
| 6 | `PoDemand` | Requested quantity of one PN for one PO: request type, requested quantity, due date, priority, external Job Numbers, requester/reason/notes. Business demand only — never production position. |
| 7 | `RouteTemplate` | Reusable route definition selectable at release. |
| 8 | `RouteStep` | Ordered step of a RouteTemplate: sequence, Area, Operation, expected duration, instructions. |
| 9 | `AssignedRoute` | Independent snapshot of a Route assigned to one QuantityFlow at release; template changes never alter it. |
| 10 | `QuantityFlow` | Traceable portion of physical PN quantity created by release; the unit that will later move. Carries derived current-position projection fields. |
| 11 | `PartMovement` | Immutable event record; this slice produces only `RECEIVED`. The sole source of truth for production state. |
| 12 | Current-position projection | Maintained, rebuildable derived state on `QuantityFlow` (`current_area_id`; `current_machine_id` stays NULL in this slice). |

Scan Station configuration (`scan_stations`) is stable application/infrastructure configuration, **not** a core domain aggregate (PROJECT_PROFILE §14 Scan Station Persistence). This slice defines the `station_id` column on `part_movements` for audit; management-initiated releases record a NULL station. The table itself is required no later than Phase 5.

---

## 3. Relationships

```
Department   1 ──── *  Area
Area         1 ──── *  Operation
PartNumber   1 ──── *  PoDemand
PurchaseOrder 1 ─── *  PoDemand
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

PartMovement carries **no** `po_demand_id`. A release may be initiated from a PoDemand UI context, and that context may be captured informationally in `metadata` for audit display, but Movement remains shop-floor activity at PN + QuantityFlow + quantity level. PoDemand does not own Movement; PoAllocation (later slice) remains separate from both.

---

## 4. Business Invariants

1. PN identity is stable and unique: `part_number` and `barcode_value` are unique arbitrary strings, never derived from display names, never reused.
2. Saving or editing PurchaseOrder/PoDemand never creates, changes, or destroys production quantity.
3. Production quantity enters the system **only** through an explicit release command that appends a `RECEIVED` Movement. Every QuantityFlow's first Movement is its `RECEIVED`.
4. All quantities are positive integers: PoDemand requested quantity > 0, flow quantity > 0, Movement quantity > 0.
5. `RECEIVED.quantity` equals the created flow's quantity.
6. A flow occupies exactly one current Area; multi-Area distribution of a PN is represented by multiple flows.
7. Release never merges with existing active quantity and never creates additional quantity implicitly; releasing a PN with active quantity requires explicit UI confirmation of intent (§8).
8. Movement history is immutable and append-only; current state must be reconstructable from it (§15).
9. The Movement insert, AssignedRoute snapshot, QuantityFlow creation, and projection update commit atomically or not at all (§13).
10. A duplicate release submission with the same `device_event_id` returns the original result and creates nothing (§14).
11. Inactive entities (PN, Area, Operation, RouteTemplate) never accept a release.

---

## 5. PurchaseOrder and PoDemand Validation

- `po_number` is a non-empty arbitrary string, unique among PurchaseOrders. Creating an existing PO Number surfaces the existing PO (no duplicate); imports arrive in a later phase and must remain idempotent against the same rule.
- `received_date` is required.
- Each PoDemand requires: existing active `part_number_id`, `request_type IN ('NEW','REWORK','MODIFY')` (default `NEW`), `requested_quantity > 0`, and a due date when business rules require one.
- `job_numbers` stores external Job Numbers as data (list of arbitrary strings) — searchable, displayable, sortable; never a domain aggregate.
- `priority_rank` is nullable; Hot ranking management is a later phase but the column belongs to PoDemand from the start.
- Editing PoDemand is permitted (Admin/Manager per PROJECT_PROFILE §8.3); edits are audited (§16) and never touch QuantityFlow or Movement.

---

## 6. PartNumber Creation and Barcode Uniqueness

- Creating a PN captures `part_number` (verbatim arbitrary string) and generates `barcode_value` (`PF:PN:<stable-id>`, PROJECT_PROFILE §9).
- Both carry UNIQUE constraints; the barcode identifies only the PN and encodes no PO, quantity, Route, or location context.
- Barcode values are never derived from mutable display names and never reused after deactivation.
- An inactive PN is visible in lookup but flagged; it cannot be added to new demand or released without reactivation.

---

## 7. Separation of Demand Save from Production Release

Two distinct commands with distinct transaction boundaries:

- **Save demand** writes `purchase_orders` / `po_demands` only. No QuantityFlow, no Movement, no projection change. The UI labels demand as "business demand — separate from production".
- **Release to production** (§8) is never triggered implicitly by saving, importing, or editing demand.

This preserves the canonical boundary: PurchaseOrder and PoDemand represent business demand and never define current production position; production release explicitly introduces physical quantity.

---

## 8. Explicit Release Command

Input: PN, release quantity, Route (template selection or confirmation of a default), starting Area + Operation confirmation, initiating context (PoDemand id for audit display only), `device_event_id`.

Steps (one transaction, §13):

1. Validate PN active, Area active and configured as a starting Area, Operation valid for that Area, RouteTemplate active, quantity > 0.
2. If the PN has active flows, require the request to carry the explicit confirmation flag set by the UI after showing the existing distribution; otherwise reject. Never auto-create or auto-merge.
3. Create the QuantityFlow.
4. Snapshot the AssignedRoute (§10).
5. Append the `RECEIVED` PartMovement (§11).
6. Update the current-position projection (§15).
7. Commit and return: flow id, route snapshot id, starting Area, Operation, quantity, Movement id.

---

## 9. QuantityFlow Creation

- Columns per PROJECT_PROFILE §8.7: `id`, `part_number_id`, `quantity`, `status` (`ACTIVE` on creation), `parent_flow_id` (NULL in this slice; reserved for SPLIT), `created_at`, `closed_at` (NULL).
- Plus maintained projection columns: `current_area_id`, `current_machine_id` (NULL throughout this slice), `updated_at`.
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
- `from_area_id` NULL; `to_area_id` = starting Area; `operation_id` = resolved starting Operation.
- `quantity` = flow quantity; composite FK guarantees PN agreement.
- `station_id` NULL for management-initiated release (Scan Station releases are not part of this slice).
- `worker_id`, `scan_session_id`, `route_step_id` nullable; `route_step_id` may reference the snapshot's first step.
- `occurred_at`, `server_received_at` — see §14.
- `device_event_id` NOT NULL UNIQUE — idempotency key (§14).
- `metadata` may capture the initiating PoDemand context for audit display; it creates no ownership (§3).
- Immutable: insert-only; UPDATE/DELETE revoked from the application role plus a raise-on-write trigger, per the PostgreSQL-constraints-first rule.

---

## 12. Starting Area and Operation Resolution

- The starting Area comes from Department/Route configuration (PROJECT_PROFILE §21): the Route snapshot's first step, defaulting per configuration (e.g. Material). The release UI confirms it; it is never guessed.
- The Operation is resolved from the starting Area's configuration: a single supported Operation resolves automatically; multiple supported Operations require explicit confirmation in the release flow (ambiguity blocks the write until confirmed).
- Operation therefore exists in the schema **before** the first Operation-bearing Movement — `RECEIVED` records it from day one.

---

## 13. Transaction Boundary

One release submission = one database transaction:

```
BEGIN
  idempotency check on device_event_id (§14)
  validate PN / Area / Operation / RouteTemplate / quantity
  active-quantity confirmation check
  INSERT quantity_flows
  INSERT assigned_routes + assigned_route_steps
  INSERT part_movements (RECEIVED)
  UPDATE quantity_flows projection (current_area_id = starting Area)
COMMIT
```

Any failure rolls back everything: a Movement is never recorded without its flow, snapshot, and projection, and vice versa. Demand save is a separate, earlier transaction (§7). No external side effects live inside the transaction.

---

## 14. Idempotency and Retry Behavior

- The client generates one `device_event_id` (UUID) per release submission and reuses it on every transport retry (timeout, connection reset, unknown outcome). `UNIQUE (device_event_id)` guarantees at-most-once recording; a replayed submission returns the original outcome unchanged.
- A **new** release intent gets a new `device_event_id`; it is then caught by the active-quantity confirmation rule (§8.2), so accidental double release still requires explicit human confirmation.
- Synchronous online semantics: `occurred_at` and `server_received_at` may both be server-assigned and equal. There is **no local offline event queue**; a submission that cannot reach the server fails visibly and is blocked while disconnected (GUI_DESIGN §3.6). `device_event_id` serves request idempotency today and remains compatible with any future, separately approved offline design.

---

## 15. Derived Current-Position Projection

- `quantity_flows.current_area_id` (and later `current_machine_id`) are maintained projections updated inside the Movement transaction — a performance measure for hot read paths (Area inventory, boards).
- PartMovement history remains the source of truth: the projection value is defined as the `to_area_id` of the flow's latest Movement, and a replay procedure must be able to rebuild (or assert) every projection from Movement history alone.
- Nothing reads the projection as authority for correctness-critical decisions without holding the flow's row lock inside a transaction.

---

## 16. Audit Requirements

Auditable in this slice (PROJECT_PROFILE §27):

- PO creation and edits; PoDemand creation and edits (who, when, what changed).
- PN creation, including barcode issuance.
- Production release: the full result (flow, snapshot, `RECEIVED` Movement) plus the initiating user and PoDemand context.
- The `RECEIVED` Movement itself is the immutable production record.

Historical records never disappear; demand edit history must not rewrite prior values silently.

---

## 17. Database Constraints and Indexes

**`departments`** — PK `id`; `UNIQUE (name)`; `is_active`.

**`areas`** — PK `id`; `department_id` FK; `UNIQUE (barcode_value)` where assigned; `is_terminal`, `is_active`, `machine_assignment_mode`, `worker_identification_mode` (configuration columns exist; only starting-Area use is exercised here).

**`operations`** — PK `id`; `area_id NOT NULL` FK; `UNIQUE (area_id, code)`; `is_active`.

**`part_numbers`** — PK `id`; `UNIQUE (part_number)`; `UNIQUE (barcode_value)`; both NOT NULL; `is_active NOT NULL DEFAULT true`.

**`purchase_orders`** — PK `id`; `UNIQUE (po_number)`; `po_number`, `received_date` NOT NULL; `status`.

**`po_demands`** — PK `id`; FKs `purchase_order_id`, `part_number_id` NOT NULL; `request_type NOT NULL CHECK (request_type IN ('NEW','REWORK','MODIFY'))`; `requested_quantity int NOT NULL CHECK (requested_quantity > 0)`; `allocated_quantity int NOT NULL DEFAULT 0 CHECK (allocated_quantity >= 0)`; `priority_rank` nullable; `job_numbers`; index `(purchase_order_id)`, index `(part_number_id)`.

**`route_templates`** — PK `id`; `UNIQUE (name, version)`; `is_active`.

**`route_steps`** — PK `id`; `route_template_id NOT NULL` FK; `UNIQUE (route_template_id, sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable.

**`assigned_routes`** — PK `id`; `quantity_flow_id NOT NULL UNIQUE` FK (one snapshot per flow in this slice); `source_route_template_id` FK nullable (informational).

**`assigned_route_steps`** — PK `id`; `assigned_route_id NOT NULL` FK; `UNIQUE (assigned_route_id, sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable.

**`quantity_flows`** — PK `id`; `part_number_id NOT NULL` FK; `quantity int NOT NULL CHECK (quantity > 0)`; `status NOT NULL DEFAULT 'ACTIVE'`; `parent_flow_id` FK nullable; `current_area_id NOT NULL` FK; `current_machine_id` FK nullable (NULL in this slice); `UNIQUE (id, part_number_id)` (composite-FK target); index `(part_number_id) WHERE status = 'ACTIVE'`; index `(current_area_id)`.

**`part_movements`** — PK `id BIGSERIAL` (event order); `quantity_flow_id`, `part_number_id` NOT NULL with composite FK `(quantity_flow_id, part_number_id)` → `quantity_flows (id, part_number_id)`; `movement_type NOT NULL CHECK (movement_type IN ('RECEIVED'))` (widens additively); `quantity int NOT NULL CHECK (quantity > 0)`; `from_area_id` FK nullable, `to_area_id NOT NULL` FK; shape check `(movement_type = 'RECEIVED' AND from_area_id IS NULL)`; `operation_id` FK nullable; `station_id` FK nullable; `worker_id`, `scan_session_id`, `route_step_id` nullable; `occurred_at timestamptz NOT NULL`; `server_received_at timestamptz NOT NULL`; `device_event_id NOT NULL`, `UNIQUE (device_event_id)`; `metadata jsonb`; index `(quantity_flow_id, id)`; immutability guard (revoke UPDATE/DELETE + raise trigger).

**`scan_stations`** — PK `id`; `UNIQUE (code)`; `area_id NOT NULL` FK; `is_active`. Configuration table only (§2); first exercised by Phase 5.

Cross-row invariants PostgreSQL cannot express declaratively (projection agrees with latest Movement; first Movement of a flow is `RECEIVED`) are enforced by the transaction protocol (§13) and verified by replay/reconciliation checks (§15).

---

## 18. Explicitly Deferred Capabilities

| Deferred | Arrives | Additive path |
|---|---|---|
| Scan Station transfer, `TRANSFERRED` | Phase 5 | widen movement-type check; use `scan_stations` + `station_id` |
| Machine assignment, sessions, `ASSIGNED_TO_MACHINE` / `RELEASED_FROM_MACHINE` | Phase 6 | `machines` table; nullable machine columns already reserved |
| Area ownership modes beyond queue | Phase 7 | Area configuration columns already present |
| SPLIT / MERGED, partial movement | Phase 8 | `parent_flow_id` already present; widen type check |
| Undo / corrections, `REVERSED`, `reverses_movement_id` | Phase 9 | append-only model already assumes it |
| Stockroom, `STOCKED`, PoAllocation | Phase 10 | new tables; Allocation already separate from Movement by design |
| Monitoring read models | Phase 11 | movement-derived queries |
| Priority / Hot management UI | Phase 12 | `priority_rank` column already present |
| Administration UI | Phase 13 | master-data tables already present |
| Authentication and roles | Phase 14 | no schema coupling to Movement |
| File-based PO import | Phase 15 | reuses §5 validation idempotently |
| Worker identification, ScanSession persistence | Phase 6+ | nullable columns already reserved |
| ERP fields/synchronization, offline synchronization | Deferred (unapproved) | isolated integration boundary; `device_event_id` compatible |

---

## 19. Acceptance Criteria

1. Saving a PO with PoDemand creates no QuantityFlow, no PartMovement, and no projection change.
2. Creating a new PN issues a unique barcode; duplicate PN or barcode values are impossible (constraint-verified).
3. A release creates exactly one QuantityFlow, one AssignedRoute snapshot, one `RECEIVED` Movement, and a consistent projection — atomically.
4. Releasing with an invalid or inactive PN, Area, Operation, RouteTemplate, or quantity ≤ 0 is rejected with no write.
5. Releasing a PN that already has active quantity without the explicit confirmation flag is rejected with no write; with confirmation it creates a separate flow and never merges.
6. Retrying a release with the same `device_event_id` returns the original result and creates nothing.
7. Editing a RouteTemplate after release does not change any AssignedRoute snapshot.
8. The projection rebuild procedure reproduces `current_area_id` for every flow from Movement history alone.
9. `part_movements` rows cannot be updated or deleted by the application role (trigger/permission verified by test).
10. Conservation holds: Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN).

---

## 20. Remaining Uncertainty

Only items already tracked in PROJECT_PROFILE §31 touch this slice, and none blocks it:

- **§31.2** (return from stock to active production) and **§31.3** (scrap/reject Movement types) may later widen the movement-type enum — additive.
- **§31.5** (offline scan synchronization) — this slice assumes synchronous online semantics (§14); `device_event_id` was chosen so a future approved offline design would not require renaming.
- Whether a due date is mandatory on every PoDemand or only business-expected is not fixed by PROJECT_PROFILE; this design keeps the column required-by-validation-rule rather than by constraint, so either policy is configurable without migration. No new business entities are invented to resolve this.
