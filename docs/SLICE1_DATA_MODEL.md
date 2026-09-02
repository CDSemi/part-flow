# Slice 1 — Manual Work Order Intake and Production Release Data Model

> **Language:** English is the source of truth. [Tiếng Việt](./vi/SLICE1_DATA_MODEL.md).

> **Status:** Implemented. This document is the canonical design and contract of the Phase 4 slice, not a proposal: the schema it specifies is migrated (`0004_phase4_audit`, `0005_phase4_release_index`), the commands it specifies are implemented in the backend Application layer, and its Acceptance Criteria (§19) are covered by tests. It stays subordinate to `PROJECT_PROFILE.md` and `GUI_DESIGN.md`; where implementation and this document disagree, the disagreement is a defect in one of them, never a licence to invent behavior.
> **Scope:** Roadmap Phase 4 vertical slice — *manually enter a Work Order and its Work Order Demand, then explicitly release production quantity into the configured starting Area*.
> **Basis:** `docs/PROJECT_PROFILE.md` (v21, canonical — §7, §8, §13, §17, §18, §21 Work Orders, §24–§25, §28), `docs/IMPLEMENTATION_ROADMAP.md` (Phases 3, 3.5, 4), `docs/GUI_DESIGN.md` §11 Work Orders, `CLAUDE.md`.

---

## 1. Slice Goals and Non-Goals

**Goals.** Implement the first business vertical slice end to end:

1. Create or find a WorkOrder.
2. Create or find a PartNumber (with its unique PartFlow barcode).
3. Create or update WorkOrderDemand rows for the Work Order.
4. Save business demand **without** creating production quantity.
5. Provide a separate explicit **production release** command that creates a QuantityFlow with its route mode (`FLOATING` by default — no AssignedRoute; `PLANNED` — with an AssignedRoute snapshot), appends an immutable `RECEIVED` PartMovement, and establishes the current position — transactionally and idempotently.

**Non-goals (explicitly out of this slice).** Scan Station transfer workflows, Machine assignment, Worker/Machine sessions, SPLIT/MERGED, Undo/corrections, Stockroom completion, WorkOrderAllocation, file-based Work Order import, authentication/roles, ERP integration, offline behavior of any kind. See §18.

---

## 2. Required Entities and Responsibilities

| # | Entity | Responsibility in this slice |
|---|--------|------------------------------|
| 1 | `Department` | Organizational owner of Areas; configuration context (initial data: Machine Shop). |
| 2 | `Area` | Stable physical location identity. Provides the configured starting Area for release; destination of `RECEIVED`. |
| 3 | `Operation` | Work supported by an Area. The starting Area's Operation is resolved or confirmed at release and recorded on the `RECEIVED` Movement. |
| 4 | `PartNumber` | Optional current-metadata master record for a canonical PN. The PN string itself is the stable production identity (PROJECT_PROFILE §7/§8.1); production rows keep their own canonical PN value and never depend on this record. |
| 5 | `WorkOrder` | Business order shell: nullable external Work Order Number (arbitrary opaque string; `NULL` when unknown — rendered `—`, §5), received date, nullable due date, status. |
| 6 | `WorkOrderDemand` | Requested quantity of one PN for one Work Order: request type, requested quantity, due date, priority, external Job Numbers, requester/reason/notes. Business demand only — never production position. |
| 7 | `RouteTemplate` | Reusable route definition selectable at release. |
| 8 | `RouteStep` | Ordered step of a RouteTemplate: sequence, Area, Operation, expected duration, instructions. |
| 9 | `AssignedRoute` | Independent snapshot of a Route assigned to one **PLANNED** QuantityFlow at release; template changes never alter it. Optional — Floating flows (the default) have none. |
| 10 | `QuantityFlow` | Traceable portion of physical PN quantity created by release; the unit that will later move. Carries its `route_mode` (`FLOATING` default / `PLANNED`), its nullable `assigned_route_id` snapshot reference (set only for `PLANNED`, PROJECT_PROFILE §8.7), and derived current-position projection fields. |
| 11 | `PartMovement` | Immutable event record; this slice produces only `RECEIVED`. The sole source of truth for production state. |
| 12 | Current-position projection | Maintained, rebuildable derived state on `QuantityFlow` (`current_area_id`; `current_machine_id` arrives with Phase 6). |
| 13 | Audit event | Generic append-only audit row for master-data and business-demand changes (§16). Persistence infrastructure, not a domain aggregate. |

Scan Station configuration (`scan_stations`) is stable application/infrastructure configuration, **not** a core domain aggregate (PROJECT_PROFILE §15 Scan Station Persistence). Management-initiated release does not involve a Scan Station, so this slice creates neither the `scan_stations` table nor the `station_id` column. The `scan_stations` table is created by the Phase 3.5 minimum environment setup (IMPLEMENTATION_ROADMAP Phase 3.5); `station_id` remains the canonical name of the Movement column, added by the Phase 5 migration that introduces station-recorded transfers.

---

## 3. Relationships

```
Department   1 ──── *  Area
Area         1 ──── *  Operation
WorkOrder 1 ─── *  WorkOrderDemand
RouteTemplate 1 ─── *  RouteStep
QuantityFlow 1 ──── 0..1 AssignedRoute      (snapshot; via quantity_flows.assigned_route_id, PLANNED only)
AssignedRoute 1 ─── *  AssignedRouteStep    (snapshot rows)
QuantityFlow 1 ──── *  PartMovement
Area         1 ──── *  PartMovement (to)    (required; `RECEIVED` has from_area_id NULL)
Operation    1 ──── *  PartMovement         (nullable; recorded when resolved)
```

The PN is carried **by value** — the canonical uppercase PN string (PROJECT_PROFILE §7) — not through a surrogate `part_number_id`:

```
WorkOrderDemand.part_number   (canonical PN, kept by the demand)
QuantityFlow.part_number      (canonical PN, kept by the flow)
PartMovement.part_number      (canonical PN, kept by the Movement)
```

There is **no foreign key from production rows to `part_numbers`**: the PartNumber master is optional current metadata and may be hard-deleted without touching production data (PROJECT_PROFILE §8.1/§28). PN consistency between a Movement and its flow is still enforced structurally, by a composite foreign key `(quantity_flow_id, part_number)` referencing `quantity_flows (id, part_number)` (§17).

PartMovement carries **no** `work_order_demand_id`. A release may be initiated from a WorkOrderDemand UI context, and that context may be captured informationally in `metadata` for audit display, but Movement remains shop-floor activity at PN + QuantityFlow + quantity level. WorkOrderDemand does not own Movement; WorkOrderAllocation (later slice) remains separate from both.

---

## 4. Business Invariants

1. PN identity is the canonical PN string itself (uppercase, whitespace-free — PROJECT_PROFILE §7): stable, unique by construction, never derived from display names. Production rows carry that canonical value directly and stay valid whether or not a PartNumber master record exists.
2. Saving or editing WorkOrder/WorkOrderDemand never creates, changes, or destroys production quantity.
3. Production quantity enters the system **only** through an explicit release command that appends a `RECEIVED` Movement. Every QuantityFlow's first Movement is its `RECEIVED`. Every flow carries a route mode; a `PLANNED` flow has exactly one AssignedRoute snapshot, a `FLOATING` flow has none — its route trace is derived from Movement history alone.
4. All quantities are positive integers: WorkOrderDemand requested quantity > 0, flow quantity > 0, Movement quantity > 0.
5. `RECEIVED.quantity` equals the created flow's quantity.
6. A flow occupies exactly one current Area; multi-Area distribution of a PN is represented by multiple flows.
7. Release never merges with existing active quantity and never creates additional quantity implicitly; releasing a PN with active quantity requires explicit UI confirmation of intent (§8).
8. Movement history is immutable and append-only; current state must be reconstructable from it (§15).
9. The QuantityFlow creation (carrying its initial `current_area_id` projection), AssignedRoute snapshot, and Movement insert commit atomically or not at all (§13).
10. A duplicate release submission with the same `device_event_id` and the same normalized request returns the original result and creates nothing; the same `device_event_id` with a different normalized request is an idempotency conflict and creates nothing (§14).
11. Inactive entities (Area, Operation, RouteTemplate) never accept a release. (The PartNumber master has no active/inactive lifecycle — the canonical PN string is always usable, with or without a master record.)

---

## 5. WorkOrder and WorkOrderDemand Validation

- `work_order_number` is a **nullable** arbitrary opaque string (PROJECT_PROFILE §7 Work Order). When the user confirms saving with a blank Work Order Number, `NULL` is stored on an internal Work Order: the UI renders `—` (the placeholder is never persisted), labels the row as an internal Work Order without an external number, and allows adding the real number later through an audited edit. Multiple Work Orders may hold `NULL` simultaneously; uniqueness applies to non-null numbers only (partial unique index, §17). No temporary Work Order Number is ever generated. Entered Work Order Numbers are stored verbatim, never reformatted. Creating an existing (non-null) Work Order Number surfaces the existing Work Order (no duplicate) — duplicate handling applies only when a Work Order Number was entered; imports arrive in a later phase and must remain idempotent against the same rule.
- `received_date` is required and defaults to the current date during manual creation.
- `work_orders.due_date` is nullable: a missing Work Order due date is valid data, not a validation error (PROJECT_PROFILE §8.2). It is an entry default for demand-line due dates only.
- Each WorkOrderDemand requires: a valid canonical `part_number` (normalized to uppercase, whitespace rejected — PROJECT_PROFILE §7; the PartNumber master metadata record is created on first valid use but is never a dependency of the demand row), `request_type IN ('NEW','MODIFY')` (manual entry defaults to `NEW`; Scan Station intake defaults to `MODIFY` — Repair is a movement intent, never a request type), and `requested_quantity > 0`. `due_date` is nullable — a missing due date is valid data, never a validation error, and never blocks saving (PROJECT_PROFILE §8.3).
- **Canonical demand ordering key** (PROJECT_PROFILE §18), for every consumer that orders demand: `priority_rank` (Hot rank first), then `due_date` ascending with **NULLS LAST**, then the parent WorkOrder's `received_date` ascending for undated demand, then a stable deterministic tie-breaker (creation order / internal `id`). Slice 1 performs no such ordering itself; supporting indexes arrive with the phases that consume the ordering (allocation, boards, priority — Phases 10–12).
- A canonical PN appears **at most once** among the current WorkOrderDemand rows of one WorkOrder: the same PN on the same Work Order is one demand line whose quantity is edited, never a second line (GUI_DESIGN §11.2/§11.3 — the UI focuses the existing line). There is deliberately **no** unique index for this (§17 defines none): the rule is enforced by the Application layer, which therefore has to serialize it against itself — a save that adds demand lines takes the parent WorkOrder row lock and re-reads that Work Order's canonical PN set **after** the lock is granted, so two concurrent saves adding the same PN cannot both pass a pre-lock snapshot. The loser is refused with the ordinary duplicate-demand error and writes nothing. Lock order stays demand (ascending id) → WorkOrder, the same order the demand-removal command takes, and the release command never contradicts it.
- `job_numbers` stores external Job Numbers as data (list of arbitrary strings) — searchable, displayable, sortable; never a domain aggregate.
- `priority_rank` is nullable; Hot ranking management is a later phase but the column belongs to WorkOrderDemand from the start.
- Editing WorkOrderDemand is permitted (Admin/Manager per PROJECT_PROFILE §8.3); edits are audited (§16) and never touch QuantityFlow or Movement. A line that has released production quantity keeps a **restricted** edit (§8a; PROJECT_PROFILE §13; GUI_DESIGN §11), enforced by the Application layer and not only by the UI: `requested_quantity`, `due_date` and `job_numbers` stay editable, `requested_quantity` may never fall below `max(released_quantity, allocated_quantity)`, and `request_type`, `requester`, `reason` and `notes` are refused (the `part_number` is not editable on any saved line). Removal of such a line stays refused by PROJECT_PROFILE §13.

---

## 6. PartNumber Normalization, Creation, and Barcode

- Every PN entering the system is normalized first (PROJECT_PROFILE §7): leading/trailing whitespace is **trimmed**; after trimming the value must be non-empty and contain **no internal whitespace** (space, tab, newline — rejected, never silently stripped), and is canonicalized to **UPPERCASE**. `abc-123`, `ABC-123`, `AbC-123`, `" ABC-123 "` are all the canonical PN `ABC-123`; `"ABC 123"` and `"ABC\t123"` are invalid.
- A PartNumber master record is **created on first valid use** (PROJECT_PROFILE §8.1): no preloaded catalog is required. The master is optional current metadata keyed by the canonical PN; production rows never reference it by foreign key, so it can be hard-deleted (and later recreated for the same canonical PN) without touching production data (PROJECT_PROFILE §28).
- The PN barcode carries the canonical PN itself: `PF:PN:<part-number>` (PROJECT_PROFILE §10). Because the PN is canonical uppercase, the barcode value is fully derived — no separately stored, separately unique barcode key is needed for PNs. The barcode identifies only the PN and encodes no Work Order, quantity, Route, or location context.

---

## 7. Separation of Demand Save from Production Release

Two distinct commands with distinct transaction boundaries:

- **Save demand** writes `work_orders` / `work_order_demands` only. No QuantityFlow, no Movement, no projection change. The UI labels demand as "business demand — separate from production".
- **Release to production** (§8) is never triggered implicitly by saving, importing, or editing demand.

This preserves the canonical boundary: WorkOrder and WorkOrderDemand represent business demand and never define current production position; production release explicitly introduces physical quantity.

---

## 8. Explicit Release Command

Input: PN, release quantity, Route Mode (`FLOATING` default; `PLANNED` with a template selection), starting Area + Operation confirmation, initiating context (WorkOrderDemand id for audit display only), `device_event_id`.

Steps (one transaction, §13):

1. Validate the canonical PN (normalized: trimmed, uppercase, no internal whitespace), Area active and configured as a starting Area — a **terminal** Area (`areas.is_terminal`, the Stockroom end of the flow, PROJECT_PROFILE §18) is never one and is refused in the Application layer, not only in the release UI — Operation valid for that Area, RouteTemplate active **when `PLANNED`**, quantity > 0 and within the initiating demand's remaining quantity (§8a). The PartNumber master has no active/inactive state and its existence is never a release precondition.
2. If the PN has active flows, require the request to carry the explicit confirmation flag set by the UI after showing the existing distribution; otherwise reject. Never auto-create or auto-merge.
3. Snapshot the AssignedRoute **only for a `PLANNED` release** (§10); a `FLOATING` release creates none.
4. Create the QuantityFlow with its `route_mode`, its `assigned_route_id` (the snapshot's id for `PLANNED`, NULL for `FLOATING` — PROJECT_PROFILE §8.7) and its initial projection: `current_area_id` = the confirmed starting Area (§9, §15).
5. Append the `RECEIVED` PartMovement — referencing the snapshot's first step for a `PLANNED` flow (`assigned_route_step_id` stays NULL for `FLOATING`) — and recording the resolved Operation; its `metadata` carries the request fingerprint (§14) plus, informationally, the initiating actor and optional WorkOrderDemand context (§11).
6. Commit and return: flow id, route mode, route snapshot id when planned, starting Area, Operation, quantity, Movement id.

The release transaction appends **no** generic `audit_events` row: the `RECEIVED` Movement is itself the immutable production audit record (§16).

### 8a. Partial and repeated release of one demand

A WorkOrderDemand may be released in **several parts** — 20 of 50, then 12, then 18. Each part is an independent release: its own `device_event_id`, its own QuantityFlow, its own `RECEIVED` Movement, and (from the second part onward, because the PN then has active quantity) its own explicit confirmation under §8.2. Nothing is ever merged.

- `released_quantity(demand)` is **derived**: the sum of `RECEIVED.quantity` over the Movements whose `metadata.context.work_order_demand_id` is that demand (§11/§14). There is no stored counter, no column, and no migration — `part_movements` is append-only, so the value can never regress or drift, and it needs no reconciliation.
- `remaining_quantity = requested_quantity − released_quantity` is the **hard server-side cap**: a release beyond it, or any release once it reaches 0, is refused and creates nothing. The demand row is locked `FOR UPDATE` for the release transaction, so two concurrent partial releases of the same demand serialize and can never jointly over-release.
- A demand with any released quantity keeps the **restricted edit** of PROJECT_PROFILE §13 (GUI_DESIGN §11): the demand-edit command (§7) accepts `requested_quantity`, `due_date` and `job_numbers` for such a line and refuses `request_type`, `requester`, `reason` and `notes`; `requested_quantity` may not fall below `max(released_quantity, allocated_quantity)`, so lowering it below what is already released is impossible, while raising it simply restores remaining quantity (and the Work Order's derived `OPEN`). A refused `line_edits` entry writes nothing — not even the valid fields travelling with it, since one save is one transaction. The edit command takes the SAME row lock as the release before it decides, and recomputes the released quantity under it, so the two serialize in either arrival order — a release that commits first is seen by the edit (which then refuses a quantity below it), and an edit that commits first is seen by the release (which re-reads the row and caps on the new requested quantity). `released_quantity > requested_quantity` is therefore unreachable, not merely unlikely. Removal stays refused by PROJECT_PROFILE §13. The Work Order header/number edit is unaffected.
- Read models expose `released_quantity` and `remaining_quantity` per demand; a Work Order reads `RELEASED` only when **every** current demand line has `remaining_quantity = 0` — a partly released line keeps it `OPEN`, because its remainder is still releasable.

---

## 9. QuantityFlow Creation

- Columns per PROJECT_PROFILE §8.7, restricted to those this slice uses: `id`, `part_number` (the canonical uppercase PN, kept by the flow itself), `quantity`, `status` (`ACTIVE` on creation), `route_mode` (`FLOATING` default / `PLANNED`), `assigned_route_id` (nullable snapshot reference — set to the AssignedRoute's id exactly when `route_mode = 'PLANNED'`, NULL for `FLOATING`), `created_at`, `closed_at` (NULL). Lineage is **not** part of this slice; Phase 8 realized the canonical `parent_flow_id` idea as the append-only `quantity_flow_lineage` edge table (parent → child per SPLIT child and per MERGED source), because a single self-reference column cannot express an N → 1 merge (§18).
- Plus maintained projection columns: `current_area_id` (NOT NULL; set by the INSERT itself to the confirmed starting Area — a QuantityFlow row never exists without a valid current Area) and `updated_at`. `current_machine_id` is the canonical name of the Machine projection column; it is **not** created in this slice and arrives with the Phase 6 migration (§18).
- Flow quantity is immutable within this slice (no SPLIT/MERGED/QUANTITY_ADJUSTED yet), so conservation is verifiable as Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN). This stays true beyond the slice: a Phase 9 `QUANTITY_ADJUSTED` addition introduces a NEW flow rather than editing an existing quantity, so a flow's quantity never changes outside its lineage.

---

## 10. AssignedRoute Snapshot Creation (`PLANNED` releases only)

- A `PLANNED` release copies the selected RouteTemplate's steps into `assigned_routes` + `assigned_route_steps` (sequence, area_id, operation_id, expected_duration, instructions). A `FLOATING` release creates no snapshot — the flow's route trace is derived from Movement history (PROJECT_PROFILE §17).
- The snapshot references its source template (`source_route_template_id`, informational) but is independent: later template edits never alter it (PROJECT_PROFILE §8.10).
- The flow references its snapshot through `quantity_flows.assigned_route_id` (PROJECT_PROFILE §8.7) — the only FK between the two tables; `assigned_routes` carries no reverse `quantity_flow_id` column. At most one flow per snapshot (`UNIQUE (assigned_route_id)`), and the reference is present exactly when `route_mode = 'PLANNED'` (CHECK, §17); route editing and deviations arrive with later phases.
- The snapshot's first step must match the confirmed starting Area (and Operation where the step specifies one); mismatch is a validation failure, not a silent adjustment.

---

## 11. RECEIVED PartMovement

Columns per PROJECT_PROFILE §8.11, with slice-relevant shape:

- `movement_type = 'RECEIVED'` (the only type this slice produces; the enum widens additively later).
- `from_area_id` NULL; `to_area_id` = starting Area (NOT NULL); `operation_id` = resolved starting Operation (NOT NULL — a valid release always resolves or explicitly confirms one, §12).
- `quantity` = flow quantity; composite FK guarantees PN agreement.
- `assigned_route_step_id` → `assigned_route_steps.id`, the **immutable snapshot** step — never the mutable `route_steps` template row, so template edits can never alter the route context recorded by an existing Movement. **Nullable**: it is set for a `PLANNED` flow's `RECEIVED` (the snapshot's first step, which §10 requires to match the confirmed starting Area) and NULL for a `FLOATING` flow, which has no AssignedRoute. Its agreement with the flow is a **cross-table invariant** that no ordinary PostgreSQL CHECK can express: a `PLANNED` flow's `RECEIVED` must reference a step of **that flow's own** AssignedRoute, and a `FLOATING` flow's must stay NULL — enforced by the release transaction protocol (§13) and verified by reconciliation/tests (§17). The name is deliberately not a generic `route_step_id`, which would be ambiguous between `route_steps` and `assigned_route_steps`; it refines the illustrative `route_step_id` attribute of PROJECT_PROFILE §8.11.
- `movement_reason` is the canonical column name for the typed movement intent (first value `REPAIR`, PROJECT_PROFILE §8.11); it is **not** created in this slice and arrived with the Phase 9 migration (`0010_phase9_undo_corrections`, §18), together with the free-text `reason` and `reverses_movement_id`.
- `station_id`, `worker_id`, `scan_session_id` are canonical column names for later phases (§18); they are **not** created in this slice because their owning tables do not exist yet at this migration point (`scan_stations` arrives with the Phase 3.5 environment setup, the Worker/session tables later still). Management-initiated release requires none of them.
- `occurred_at`, `server_received_at` — see §14.
- `device_event_id` NOT NULL UNIQUE — idempotency key (§14).
- `metadata` carries the deterministic request fingerprint (§14) and may informationally capture the initiating actor (until authentication exists, Phase 14) and the initiating WorkOrderDemand context for audit display; none of this creates ownership — WorkOrderDemand never owns Movement (§3).
- Immutable: insert-only; UPDATE/DELETE revoked from the application role plus a raise-on-write trigger, per the PostgreSQL-constraints-first rule. This guard binds the application at all times; the later Movement-history retention maintenance (PROJECT_PROFILE §28, roadmap Phase 16) runs through a separate privileged Admin path and is out of this slice.

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
  INSERT assigned_routes + assigned_route_steps   -- PLANNED releases only
  INSERT quantity_flows            (route_mode; assigned_route_id = snapshot id
                                    for PLANNED, NULL for FLOATING;
                                    current_area_id = confirmed starting Area)
  INSERT part_movements (RECEIVED; assigned_route_step_id = snapshot first
                         step for PLANNED, NULL for FLOATING)
COMMIT
```

The QuantityFlow is inserted complete, with its initial `current_area_id` projection already set — no intermediate invalid or projection-less row ever exists (`current_area_id` is NOT NULL). Any failure rolls back everything: a Movement is never recorded without its flow and snapshot, and vice versa — no partial release state can commit. No generic `audit_events` row is written here: the `RECEIVED` Movement is the production audit record (§16). PartMovement remains the source of truth and the projection remains rebuildable from it (§15). Demand save is a separate, earlier transaction (§7). No external side effects live inside the transaction.

---

## 14. Idempotency and Retry Behavior

- The client generates one `device_event_id` (UUID) per release submission and reuses it on every transport retry (timeout, connection reset, unknown outcome). `UNIQUE (device_event_id)` guarantees at-most-once recording.
- A duplicate `device_event_id` is safe only when it carries the **same normalized request**. On submission the server computes a deterministic **request fingerprint** — a canonical hash over at least: PartNumber, release quantity, Route Mode (and RouteTemplate when `PLANNED`), starting Area, Operation, and the initiating WorkOrderDemand context when supplied — and stores it in the `RECEIVED` Movement's `metadata` (§11). No separate idempotency table exists: the Movement row, found via `UNIQUE (device_event_id)`, is the idempotency record.
- Replay outcomes:
  - same `device_event_id` + same fingerprint → return the original committed result unchanged; create nothing;
  - same `device_event_id` + different fingerprint → return an explicit idempotency-conflict error; create nothing. A mismatch signals a client defect (an id wrongly reused for a new intent) and is never silently honored.
- A **new** release intent gets a new `device_event_id`; it is then caught by the active-quantity confirmation rule (§8.2), so accidental double release still requires explicit human confirmation.
- **Scope.** Idempotency in this slice is a property of the **release** command, because release is the one command that introduces physical quantity and the one whose retry could duplicate it. Demand save (§7) deliberately carries no idempotency key: it writes business demand only — a repeated save creates no QuantityFlow and no Movement, an entered Work Order Number is unique (§5, partial unique index), and the only repeatable outcome is a second internal Work Order holding `NULL`, which §5 explicitly permits. Adding a key there would change the intake contract without protecting quantity integrity; if file-based import (Phase 15) needs one, it arrives with the phase that needs it.
- Synchronous online semantics: `occurred_at` and `server_received_at` may both be server-assigned and equal. There is **no local offline event queue**; a submission that cannot reach the server fails visibly and is blocked while disconnected (GUI_DESIGN §3.6). `device_event_id` serves request idempotency today and remains compatible with any future, separately approved offline design.

---

## 15. Derived Current-Position Projection

- `quantity_flows.current_area_id` (joined by `current_machine_id` in Phase 6) is a maintained projection — a performance measure for hot read paths (Area inventory, boards). It is set by the QuantityFlow INSERT itself at release (in the same transaction as the `RECEIVED` Movement, §13) and updated inside the Movement transaction by later movement types (for example, the Phase 6+ `AREA_COMPLETED` clears `current_machine_id` and keeps `current_area_id` — the finished quantity waits in its Area as `READY_TO_TRANSFER`, §18).
- PartMovement history remains the source of truth: the projection value is defined as the `to_area_id` of the flow's latest Movement, and a replay procedure must be able to rebuild (or assert) every projection from Movement history alone.
- Nothing reads the projection as authority for correctness-critical decisions without holding the flow's row lock inside a transaction.

---

## 16. Audit Persistence Model

Auditable in this slice (PROJECT_PROFILE §28):

- Work Order creation and edits; WorkOrderDemand creation and edits (who, when, what changed).
- PN master creation (the barcode is derived from the canonical PN, §6 — nothing separate is issued).
- Production release — audited by the `RECEIVED` PartMovement itself, the immutable production record; the initiating actor and WorkOrderDemand context live informationally in its `metadata` (§11).

Historical records never disappear; demand edit history must not rewrite prior values silently.

**Persistence.** Two complementary mechanisms, with distinct responsibilities:

1. **PartMovement is the production audit record** — the sole source of truth for production state, replayable into projections (§15), and the sole audit record for production release and all production activity. One production action, one audit record: the release transaction writes **no** duplicate generic audit row (§13).
2. **One generic append-only table, `audit_events`, records master-data and business-demand changes only** — WorkOrder, WorkOrderDemand, and PartNumber. Its rows are descriptive history for display and accountability; they are **never** replayed to build state, and never describe production actions. This is deliberately not an event-sourcing framework.

`audit_events` shape (constraints in §17):

- `id` — BIGSERIAL PK (write order).
- `event_type` — `'CREATED'` or `'UPDATED'` in this slice; widens additively later.
- `entity_type` — `'WorkOrder'`, `'WorkOrderDemand'`, or `'PartNumber'` in this slice.
- `entity_id` — the audited row's key, stored as text. Polymorphic by design, so no FK; for `WorkOrder`/`WorkOrderDemand` it is the internal PK, for `PartNumber` it is the canonical PN string (the master's natural key). Integrity is guaranteed by writing the audit row in the same transaction as the audited change.
- `actor_reference` — nullable text. Authentication and role enforcement remain deferred (Phase 14); until authenticated users exist, this is NULL or an explicitly configured development/system actor identifier. No user table is invented in this slice; Phase 14 may migrate this to a real user reference.
- `occurred_at` — timestamptz.
- `before_data` / `after_data` — jsonb snapshots of the audited fields (`before_data` NULL for creation events).
- `metadata` — jsonb for contextual detail.

Event mapping in this slice: Work Order create/edit → `CREATED`/`UPDATED` on `WorkOrder`; WorkOrderDemand create/edit → `CREATED`/`UPDATED` on `WorkOrderDemand`; PN master creation → `CREATED` on `PartNumber` (with the canonical PN as `entity_id`). Production release maps to **no** `audit_events` row — its audit record is the `RECEIVED` Movement (§13).

Rules:

- Every audit row commits in the **same transaction** as the change it records; an audited write without its audit row (or vice versa) must be impossible.
- Audit rows are append-only: UPDATE/DELETE revoked from the application role plus a raise-on-write trigger, exactly like `part_movements`. The trigger ships with the Slice 1 migration; the application-role revocation arrives with deployment hardening once a distinct application database role exists (IMPLEMENTATION_ROADMAP Phases 3 and 16) — the same deferral as `part_movements`, and the trigger already binds every non-superuser path.
- Edits append a new `UPDATED` row; prior rows are never rewritten.

---

## 17. Database Constraints and Indexes

**`departments`** — PK `id`; `UNIQUE (name)`; `is_active`; `created_at`, `updated_at`.

**`areas`** — PK `id`; `department_id NOT NULL` FK; `name NOT NULL`; `UNIQUE (barcode_value)` where assigned; `is_active`; `created_at`, `updated_at`. `is_terminal` and `worker_identification_mode` are canonical names only — not created in this slice, added by the migrations of the phases that first use them (§18). No `machine_assignment_mode` column exists: assignment behavior follows from the Area's Machines (PROJECT_PROFILE §12).

**`operations`** — PK `id`; `area_id NOT NULL` FK; `code NOT NULL`, `UNIQUE (area_id, code)`; `name`; `is_active`; `created_at`, `updated_at`.

**`part_numbers`** — optional current-metadata master. PK `part_number text` (the canonical uppercase PN — natural key, no surrogate id) with `CHECK (part_number = upper(part_number) AND part_number !~ '\s' AND part_number <> '')` enforcing the canonical form; `created_at`, `updated_at` (no `is_active` — the PN master has no active/inactive lifecycle). The PN barcode is derived (`'PF:PN:' || part_number`, PROJECT_PROFILE §10) — no stored `barcode_value` column and no `lower(...)` expression indexes (canonical uppercase makes them unnecessary). No production table references this table by FK, so a master row can be hard-deleted (and recreated later for the same canonical PN) per PROJECT_PROFILE §28.

**`work_orders`** — PK `id` (stable internal identity — never the user-facing identifier); `work_order_number` **nullable** with a **partial unique index** (`UNIQUE (work_order_number) WHERE work_order_number IS NOT NULL`) so many internal Work Orders may hold `NULL` while non-null numbers stay unique (§5); `received_date NOT NULL`; `due_date` nullable (a missing Work Order due date is valid data, §5); `status`; `created_at`, `updated_at`. `completed_at` is the canonical done-date column (PROJECT_PROFILE §8.2 — derived from allocation events, never entered by hand); it is **not** created in this slice — it arrived with the Phase 10 allocation migration (§18) as a maintained projection of the allocation rows, with the partial index `(completed_at, id) WHERE completed_at IS NOT NULL` the keyset-paged completed history reads.

**`work_order_demands`** — PK `id`; FK `work_order_id NOT NULL`; `part_number text NOT NULL` (canonical uppercase PN kept by the demand — same canonical-form CHECK as `part_numbers`, **no FK** to the master); `request_type NOT NULL CHECK (request_type IN ('NEW','MODIFY'))`; `requested_quantity int NOT NULL CHECK (requested_quantity > 0)`; `allocated_quantity int NOT NULL DEFAULT 0 CHECK (allocated_quantity >= 0)`; `due_date` nullable (a missing due date is valid data per §5 — never required by validation rule or constraint; undated demand orders after dated demand per the canonical demand ordering key, §5); `priority_rank` nullable; `job_numbers text[] NOT NULL DEFAULT '{}'` (arbitrary external strings preserved verbatim; empty list valid; metadata only — no `Job` aggregate, no FK, and no GIN index in this slice because Slice 1 includes no Job Number search); `requester`, `reason`, `notes` nullable; `created_at`, `updated_at`; index `(work_order_id)`, index `(part_number)`.

**`route_templates`** — PK `id`; `name NOT NULL`; `description` nullable; `archived_at timestamptz` nullable (`NULL` = active; an archived template is never offered for new route assignments). There is **no `version` column and no template-versioning framework** (PROJECT_PROFILE v11 §8.8): existing `assigned_routes` snapshots preserve historical route definitions. A template ever referenced by an `assigned_routes` row is archived instead of deleted; hard `DELETE` is legitimate only for a never-referenced template. `created_at`, `updated_at`.

**`route_steps`** — PK `id`; `route_template_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (route_template_id, sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable; `expected_duration` nullable; `instructions` nullable. `preferred_machine_id` is the canonical name of the preferred-Machine reference (PROJECT_PROFILE §8.9; `preferredMachineId` in the GUI mock) — **not** created in this slice: it references `machines` (created by Phase 3.5) and arrives with the migration of the phase that first uses it.

**`assigned_routes`** — PK `id`; `source_route_template_id` FK nullable (informational); `created_at` (snapshot time). The snapshot carries **no** `quantity_flow_id` back-reference: the owning flow references it through `quantity_flows.assigned_route_id` (PROJECT_PROFILE §8.7), the single FK between the two tables.

**`assigned_route_steps`** — PK `id`; `assigned_route_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (assigned_route_id, sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable; `expected_duration` nullable; `instructions` nullable (snapshot copies of the template step fields, §10).

**`quantity_flows`** — PK `id`; `part_number text NOT NULL` (canonical uppercase PN kept by the flow — same canonical-form CHECK, **no FK** to the master); `quantity int NOT NULL CHECK (quantity > 0)`; `status NOT NULL DEFAULT 'ACTIVE'`; `route_mode NOT NULL DEFAULT 'FLOATING' CHECK (route_mode IN ('FLOATING','PLANNED'))`; `assigned_route_id` FK nullable → `assigned_routes (id)` with `UNIQUE (assigned_route_id)` (at most one flow per snapshot) and `CHECK ((route_mode = 'PLANNED') = (assigned_route_id IS NOT NULL))` (a `PLANNED` flow always references its snapshot, a `FLOATING` flow never does — PROJECT_PROFILE §8.7); `current_area_id NOT NULL` FK (set at INSERT, §13); `UNIQUE (id, part_number)` (composite-FK target); `created_at`, `updated_at`, `closed_at` nullable; index `(part_number) WHERE status = 'ACTIVE'`; index `(current_area_id)`. No lineage column exists on this table; Phase 8 added the `quantity_flow_lineage` edge table and the lifecycle checks instead (§9, §18).

**`part_movements`** — PK `id BIGSERIAL` (event order); `quantity_flow_id NOT NULL`, `part_number text NOT NULL` (canonical uppercase PN kept by the Movement — the history identifies its PN without any join to the master) with composite FK `(quantity_flow_id, part_number)` → `quantity_flows (id, part_number)`; `movement_type NOT NULL CHECK (movement_type IN ('RECEIVED'))` (widens additively); `quantity int NOT NULL CHECK (quantity > 0)`; `from_area_id` FK nullable, `to_area_id NOT NULL` FK; shape check `(movement_type = 'RECEIVED' AND from_area_id IS NULL)`; `operation_id NOT NULL` FK; `assigned_route_step_id` FK nullable → `assigned_route_steps (id)` (set for `PLANNED` flows, NULL for `FLOATING` flows, §11); `occurred_at timestamptz NOT NULL`; `server_received_at timestamptz NOT NULL`; `device_event_id NOT NULL`, `UNIQUE (device_event_id)`; `metadata jsonb`; index `(quantity_flow_id, id)`; a **partial expression index** on the release-evidence path — `((metadata['context'] ->> 'work_order_demand_id')::int) WHERE movement_type = 'RECEIVED'` — because `released_quantity` is derived from that metadata context on every Work Order read (§8a) and the history is append-only and retained for years (PROJECT_PROFILE §28), so without it the hot read path scans the whole table. The stored expression must stay identical to the one the Application layer emits (the JSONB **subscript** form; the `->` operator form is a different expression node and would not match), and the index adds **no** column, no foreign key and no stored counter — the derivation stays the single source of truth. Immutability guard (revoke UPDATE/DELETE + raise trigger).

**`audit_events`** — PK `id BIGSERIAL`; `event_type NOT NULL CHECK (event_type IN ('CREATED','UPDATED'))` (widens additively); `entity_type NOT NULL CHECK (entity_type IN ('WorkOrder','WorkOrderDemand','PartNumber'))` (widens additively); `entity_id NOT NULL` (no FK — polymorphic, §16); `actor_reference` nullable; `occurred_at timestamptz NOT NULL`; `before_data jsonb` nullable; `after_data jsonb` nullable; `metadata jsonb`; index `(entity_type, entity_id, id)`; append-only guard (revoke UPDATE/DELETE + raise trigger), per §16.

Every table above exists in and is used by this slice; the Slice 1 migration contains **no** foreign keys to tables it does not create. `scan_stations` and `machines` are environment-setup configuration created by the Phase 3.5 migration (§2, §18); `part_movements.station_id` still arrives with Phase 5 and the Machine columns with Phase 6.

Cross-row invariants PostgreSQL cannot express declaratively (projection agrees with latest Movement; first Movement of a flow is `RECEIVED`; a Movement's `assigned_route_step_id` agrees with its flow's route mode and belongs to that flow's own AssignedRoute, §11; every audited change commits with its audit row; one canonical PN at most once among a Work Order's current demand lines, §5) are enforced by the transaction protocol (§13, §16 — including the WorkOrder row lock that serializes demand-line addition) and verified by replay/reconciliation checks (§15) and concurrency tests.

---

## 18. Explicitly Deferred Capabilities

| Deferred | Arrives | Additive path |
|---|---|---|
| Environment setup: `scan_stations` and `machines` tables, `areas.is_terminal` and related configuration fields | Phase 3.5 | additive configuration migrations (minimum environment setup, IMPLEMENTATION_ROADMAP Phase 3.5); Phases 5–7 keep the production workflows that use them |
| Scan Station transfer, `TRANSFERRED` | Phase 5 — **implemented** (`0006_phase5_transfer`) | widened movement-type check; `part_movements.station_id` added (nullable FK to `scan_stations.station_id`, NULL on the Management-initiated `RECEIVED`); the single-type `received_shape` check replaced by the per-type `ck_part_movements_movement_shape`; the `TRANSFERRED` metadata carries the same `request_fingerprint` idempotency mechanism as §14 plus a `route_deviation` block for a confirmed Planned-Route deviation |
| Machine assignment (one-shot, no sessions), `ASSIGNED_TO_MACHINE` / `RELEASED_FROM_MACHINE` | Phase 6 — **implemented** (`0007_phase6_machine_assignment`) | `quantity_flows.current_machine_id` (nullable FK to `machines.id`, indexed) and the Movement Machine columns `source_machine_id` / `destination_machine_id` added; widened movement-type check; the per-type shape check pins each type's Area/Station/Machine references. `part_movements.command_sequence` (NOT NULL, default 1) numbers the Movements of one application command and `UNIQUE (device_event_id, command_sequence)` replaces `UNIQUE (device_event_id)` — the release stays a one-Movement command and its §14 idempotency is unchanged |
| Area completion — `AREA_COMPLETED`, derived `READY_TO_TRANSFER` holding state (PROJECT_PROFILE v10 §7 Area Completion) | Phase 6 (Machine Areas) — **implemented** / Phase 7 (direct processing) — **implemented** | `AREA_COMPLETED` clears `quantity_flows.current_machine_id` while `current_area_id` stays; a transfer from ON_MACHINE quantity appends `AREA_COMPLETED` (sequence 1) + `TRANSFERRED` (sequence 2) under ONE `device_event_id` in ONE transaction; the projection replay derives QUEUED / ON_MACHINE / READY_TO_TRANSFER from the latest Movement. Phase 7 (`0008_phase7_direct_processing`) widens the `AREA_COMPLETED` shape branch to allow a NULL source Machine (a completion never carries a destination Machine) |
| Direct Area processing (no Machines) | Phase 7 — **implemented** (`0008_phase7_direct_processing`) | only the `AREA_COMPLETED` shape widening above — no column: the derived holding state `PROCESSING` (an arrival Movement in an Area without active Machines) is never stored, and no `machine_assignment_mode` exists — behavior follows from the Area's Machines. `areas.worker_identification_mode` (canonical name documented, §17) is NOT added here: it belongs to Worker identification (below) |
| SPLIT / MERGED, partial movement | Phase 8 — **implemented** (`0009_phase8_split_merge`) | `SPLIT` / `MERGED` types with one shared shape branch, `quantity_flows.status` / `closed_at` lifecycle checks, and the append-only `quantity_flow_lineage` edge table (parent → child, relation, `device_event_id`) instead of a `parent_flow_id` column — one edge per SPLIT child (1 → N), one per MERGED source (N → 1) |
| Undo / corrections, `REVERSED`, `reverses_movement_id`; Repair (`movement_reason = REPAIR`), `SCRAPPED`, `QUANTITY_ADJUSTED` additions | Phase 9 — **implemented** (`0010_phase9_undo_corrections`) | append-only model already assumed it; the migration adds `movement_reason` (only `REPAIR`, only on a `TRANSFERRED`), the free-text `reason` (mandatory for Scrap, adjustments and Repair — CHECK-enforced) and `reverses_movement_id` (FK to `part_movements`, set exactly on a `REVERSED`, UNIQUE — at most one reversal per original), widens the movement-type and shape checks with `SCRAPPED` / `QUANTITY_ADJUSTED` / `REVERSED`, and widens `quantity_flows.status` with `SCRAPPED` / `REVERSED`. Undo appends compensating `REVERSED` rows for the COMPLETE command; every derivation excludes the reversed pair, so the restored state is the Movement before the undone command. `QUANTITY_ADJUSTED` introduces a NEW flow (like a `RECEIVED`, station-recorded), never editing an existing quantity |
| Stockroom, `STOCKED`, WorkOrderAllocation | Phase 10 — **implemented** (`0011_phase10_stock_allocation`) | the movement-type and shape checks widen with `STOCKED` (the same shape as a `TRANSFERRED`: two different Areas at a Station, no Machine — the terminal destination is an Application rule judged under the Area lock), `quantity_flows.status` widens with `STOCKED` (the flow closes as manufacturing-complete), `work_orders.completed_at` is added (canonical name documented, §17) as a projection of the allocation rows, and the append-only `work_order_allocations` table records allocation and reversal rows (canonical PN by value, the demand FK, `reverses_allocation_id` UNIQUE, `device_event_id` + `command_sequence`) — never referencing a Movement or a QuantityFlow: Allocation stays separate from Movement by design, and `work_order_demands.allocated_quantity` becomes a maintained projection of the demand's active allocation rows. `areas.is_terminal` is created by Phase 3.5 |
| Monitoring read models | Phase 11 | movement-derived queries |
| Priority / Hot management UI | Phase 12 | `priority_rank` column already present |
| Administration UI | Phase 3.5 (minimum environment setup) / Phase 13 (full Administration) | master-data tables already present |
| Authentication and roles | Phase 14 | no schema coupling to Movement; `audit_events.actor_reference` may migrate to a real user reference (§16) |
| File-based Work Order import | Phase 15 | reuses §5 validation idempotently |
| Worker identification, ScanSession persistence | Phase 6+ | migration adds `worker_id`, `scan_session_id` (canonical names documented, §11) and `areas.worker_identification_mode` (§17) |
| ERP fields/synchronization, offline synchronization | Deferred (unapproved) | isolated integration boundary; `device_event_id` compatible |

---

## 19. Acceptance Criteria

1. Saving a Work Order with WorkOrderDemand creates no QuantityFlow, no PartMovement, and no projection change.
2. Creating a new PN (on first valid use) normalizes the entered value to the canonical PN: `abc-123`, `AbC-123`, `ABC-123`, `" ABC-123 "` all resolve to the single canonical PN `ABC-123` (surrounding whitespace trimmed; constraint-verified — a second master row for the same canonical PN is impossible), and any value with internal whitespace after trimming (`"ABC 123"`, `"ABC\t123"`, `"ABC\n123"`) is rejected with no write. The barcode is derived as `PF:PN:<part-number>` from the canonical PN. The master creation appends a `CREATED` audit event.
3. A release creates exactly one QuantityFlow (with its route mode), one `RECEIVED` Movement, and — for a `PLANNED` release only — one AssignedRoute snapshot, atomically and with no generic audit event. A `FLOATING` release (the default) creates no AssignedRoute. If any part fails, nothing commits: no committed state can contain a QuantityFlow without its `RECEIVED` Movement, no `PLANNED` flow without its snapshot, no `FLOATING` flow with one (constraint-verified via the flow's `assigned_route_id` CHECK, §17), and no partial release state is ever observable.
4. `current_area_id` is set to the confirmed starting Area by the QuantityFlow INSERT itself; the column is NOT NULL and no post-insert projection update is required for release.
5. Every `RECEIVED` Movement records a resolved Operation (`operation_id NOT NULL`, constraint-verified). For a `PLANNED` flow it references the AssignedRoute snapshot's first step (`assigned_route_step_id` set to an `assigned_route_steps` row, never a `route_steps` row); for a `FLOATING` flow `assigned_route_step_id` is NULL.
6. Releasing with an invalid PN, an inactive Area, Operation, or RouteTemplate (when `PLANNED`), or quantity ≤ 0 is rejected with no write.
7. Releasing a PN that already has active quantity without the explicit confirmation flag is rejected with no write; with confirmation it creates a separate flow and never merges.
8. Retrying a release with the same `device_event_id` and the same normalized request (matching fingerprint) returns the original result and creates nothing.
9. Reusing a `device_event_id` with a different normalized request (mismatched fingerprint) returns an explicit idempotency-conflict error and creates nothing.
10. Editing a RouteTemplate after release does not change any AssignedRoute snapshot, and template edits never alter the route context recorded by any existing Movement.
11. The projection rebuild procedure reproduces `current_area_id` for every flow from Movement history alone.
12. `part_movements` and `audit_events` rows cannot be updated or deleted by the application role (trigger/permission verified by test).
13. Every WorkOrder and WorkOrderDemand creation or edit appends an `audit_events` row (`CREATED`/`UPDATED` with `before_data`/`after_data`) in the same transaction as the change; edits preserve all prior audit rows unchanged (append-only history, verified by test).
14. `audit_events` contains rows only for `WorkOrder`, `WorkOrderDemand`, and `PartNumber` — never for production release or any other production activity (constraint- and test-verified).
15. Conservation holds: Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN).
16. Releasing into a **terminal** Area is rejected with no write, whatever the UI offered.
17. A demand releases in parts until its remaining quantity is exhausted: each part creates its own QuantityFlow and `RECEIVED` (never a merge), the released and remaining quantities derive from Movement history alone, a release beyond the remainder — and any release once it is 0 — is rejected with no write, and the Work Order reads `RELEASED` only once every line's remainder is 0.
18. A demand line that has released quantity takes the restricted edit: its `requested_quantity` (never below the released or allocated quantity), `due_date` and `job_numbers` save with a normal `UPDATED` audit row, raising the quantity restores remaining quantity and the Work Order's derived `OPEN`, and an edit of `request_type`, `requester`, `reason` or `notes` — or a quantity below what is committed — is rejected with no write at all. Its prior audit history, its released quantity, and the Work Order header edit are unaffected.
19. No Slice 1 migration contains a foreign key to any table it does not create, nor any unused deferred column: no `machines`, `workers`, `scan_sessions`, `scan_stations`, or user-table references, and no `current_machine_id`, `parent_flow_id`, `station_id`, `worker_id`, `scan_session_id`, `reverses_movement_id`, `movement_reason`, `is_terminal`, `worker_identification_mode`, `preferred_machine_id`, or `completed_at`. (There is no `machine_assignment_mode` at all anymore — Area behavior follows from its Machines, PROJECT_PROFILE §12.)

---

## 20. Remaining Uncertainty

Only items already tracked in PROJECT_PROFILE §32 touch this slice, and none blocks it:

- **§32.1** (return from stock to active production) may later widen the movement-type enum — additive. (`SCRAPPED` is already canonical, PROJECT_PROFILE v9 §8.11, and arrived with Phase 9; `STOCKED` arrived with Phase 10 and, until §32.1 is decided, a `STOCKED` command is never undone — allocation adjustments are the correction path.)
- **§32.2** (offline scan synchronization) — this slice assumes synchronous online semantics (§14); `device_event_id` was chosen so a future approved offline design would not require renaming.
- The former due-date uncertainty is resolved: PROJECT_PROFILE v8 (§8.2, §8.3) fixes both `work_orders.due_date` and `work_order_demands.due_date` as nullable — a missing due date is valid data, never a validation error — with undated demand ordered after dated demand by the canonical demand ordering key (§5). No policy toggle or migration is needed.
