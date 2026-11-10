# Slice 1 — Manual Work Order Intake and Production Release Data Model

> **Language:** English is the source of truth. [Tiếng Việt](./SLICE1_DATA_MODEL.vi.md).

This document specifies the data model and the command contracts of the first business slice of PartFlow: entering a
Work Order and its Work Order Demand by hand, and then explicitly releasing production quantity into a configured
starting Area. It is written for developers and reviewers who change the Work Order, Part Number (PN), Quantity Flow,
Assigned Route, Part Movement or audit persistence, and for anyone who follows a source-code comment that cites it by
section number (for example `SLICE1 §14`).

It is subordinate implementation detail. `PROJECT_PROFILE.md` owns the domain terminology, business rules, invariants
and workflows; `GUI_DESIGN.md` owns the approved user interface (UI); `IMPLEMENTATION_ROADMAP.md` owns the
implementation order and the current implementation state; `DEPLOYMENT.md` owns deployment. This document never
overrides them: where it and the implementation disagree, the disagreement is a defect in one of the two, never a
licence to invent behaviour. The rules it restates come mainly from PROJECT_PROFILE §7, §8, §13, §17, §18, §21 (Work
Orders), §24 to §25 and §28, and from GUI_DESIGN §11 (Work Orders).

The schema described here is the current schema at migration head `0032_phase14_route_adjusted`. The slice's own tables
were created by the migrations `0002_phase3_domain`, `0004_phase4_audit` and `0005_phase4_release_index`; later
migrations added columns, widened checks and added tables, and each section states the current shape.

Abbreviations used throughout: Part Number (PN); primary key (PK); foreign key (FK); enterprise resource planning (ERP);
universally unique identifier (UUID); application programming interface (API); PostgreSQL binary JSON (`jsonb`);
Coordinated Universal Time (UTC).

---

## 1. Slice Goals and Non-Goals

**Goals.** The slice implements the first business workflow end to end:

1. Create or find a WorkOrder.
2. Create or find a PartNumber (with its unique PartFlow barcode).
3. Create or update the WorkOrderDemand rows of the Work Order.
4. Save business demand **without** creating production quantity.
5. Provide a separate, explicit **production release** command. The command creates a QuantityFlow with its route mode
   (`FLOATING` by default, with no AssignedRoute; or `PLANNED`, with an AssignedRoute snapshot), appends an immutable
   `RECEIVED` PartMovement, and establishes the current position. It does all of this in one transaction and is
   idempotent.

**Non-goals.** The following capabilities are outside this slice: Scan Station transfer workflows, Machine assignment,
Worker and Machine sessions, SPLIT and MERGED, Undo and corrections, Stockroom completion, WorkOrderAllocation,
file-based Work Order import, authentication and roles, ERP integration, and offline behaviour of any kind. Most of them
exist today and are specified elsewhere; §18 says where.

---

## 2. Required Entities and Responsibilities

| # | Entity | Responsibility in this slice |
|---|--------|------------------------------|
| 1 | `Department` | Organizational owner of Areas and the configuration context (initial data: Machine Shop). |
| 2 | `Area` | Stable physical location identity. It provides the configured starting Area for a release and is the destination of `RECEIVED`. |
| 3 | `Operation` | Work supported by an Area. The starting Area's Operation is resolved or confirmed at release and recorded on the `RECEIVED` Movement. |
| 4 | `PartNumber` | Optional record of current metadata for a canonical PN. The PN string itself is the stable production identity (PROJECT_PROFILE §7 and §8.1); production rows keep their own canonical PN value and never depend on this record. |
| 5 | `WorkOrder` | Business order shell: a nullable external Work Order Number (an arbitrary opaque string; `NULL` when unknown, rendered `—`, §5), a received date, a nullable due date and a status. |
| 6 | `WorkOrderDemand` | Requested quantity of one PN for one Work Order: request type, requested quantity, due date, priority, external Job Numbers, requester, reason and notes. It is business demand only and never a production position. |
| 7 | `RouteTemplate` | Reusable route definition that can be selected at release. |
| 8 | `RouteStep` | Ordered step of a RouteTemplate: sequence, Area, Operation, expected duration, preferred Machine and instructions. |
| 9 | `AssignedRoute` | Independent snapshot of a route, assigned to one **PLANNED** QuantityFlow at release. Template changes never alter it. Past steps are immutable; only unreferenced future steps change, through the audited `ROUTE_ADJUSTED` command (§16). It is optional: Floating flows (the default) have none. |
| 10 | `QuantityFlow` | Traceable portion of the physical quantity of one PN, created by release; it is the unit that later moves. It carries its `route_mode` (`FLOATING` by default, or `PLANNED`), its nullable `assigned_route_id` snapshot reference (set only for `PLANNED`, PROJECT_PROFILE §8.7), and the derived current-position projection fields. |
| 11 | `PartMovement` | Immutable event record and the sole source of truth for production state. This slice produces only `RECEIVED`. |
| 12 | Current-position projection | Maintained, rebuildable derived state on `QuantityFlow` (`current_area_id` and `current_machine_id`). |
| 13 | Audit event | Generic append-only audit row for master-data, business-demand and configuration changes (§16). It is persistence infrastructure, not a domain aggregate. |

Scan Station configuration (`scan_stations`) is stable application and infrastructure configuration, **not** a core
domain aggregate (PROJECT_PROFILE §15 Scan Station Persistence). A release started from Management involves no Scan
Station: its `RECEIVED` Movement leaves `station_id` NULL. The `scan_stations` table belongs to the minimum environment
setup, and `part_movements.station_id` belongs to the station-recorded Movements (§18).

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

The PN is carried **by value**, as the canonical uppercase PN string (PROJECT_PROFILE §7), and never through a surrogate
`part_number_id`:

```
WorkOrderDemand.part_number   (canonical PN, kept by the demand)
QuantityFlow.part_number      (canonical PN, kept by the flow)
PartMovement.part_number      (canonical PN, kept by the Movement)
```

There is **no foreign key from production rows to `part_numbers`**. The PartNumber master is optional current metadata
and may be hard-deleted without touching production data (PROJECT_PROFILE §8.1 and §28). PN consistency between a
Movement and its flow is still enforced structurally, by a composite foreign key `(quantity_flow_id, part_number)` that
references `quantity_flows (id, part_number)` (§17).

PartMovement carries **no** `work_order_demand_id`. A release is initiated from a WorkOrderDemand in the UI, and that
context is captured informationally in the Movement's `metadata` (§11), but a Movement remains shop-floor activity at
the level of PN, QuantityFlow and quantity. WorkOrderDemand does not own Movement, and WorkOrderAllocation remains
separate from both.

---

## 4. Business Invariants

1. PN identity is the canonical PN string itself (uppercase and free of whitespace, PROJECT_PROFILE §7). It is stable,
   unique by construction and never derived from display names. Production rows carry that canonical value directly
   and stay valid whether or not a PartNumber master record exists.
2. Saving or editing a WorkOrder or WorkOrderDemand never creates, changes or destroys production quantity.
3. Within this slice, production quantity enters the system **only** through an explicit release command that appends
   a `RECEIVED` Movement. (Outside this slice, PROJECT_PROFILE also introduces quantity through a Scan Station receipt,
   which likewise appends a `RECEIVED`, and through an explicit `QUANTITY_ADJUSTED` addition.) Every QuantityFlow
   created by a release has its `RECEIVED` as its first Movement. Every flow carries a route mode: a
   `PLANNED` flow has exactly one AssignedRoute snapshot, and a `FLOATING` flow has none; its route trace is derived
   from Movement history alone.
4. All quantities are positive integers: WorkOrderDemand requested quantity > 0, flow quantity > 0, Movement quantity
   > 0.
5. `RECEIVED.quantity` equals the created flow's quantity.
6. A flow occupies exactly one current Area. When a PN is spread over several Areas, each Area holds its own flow.
7. Release never merges with existing active quantity and never creates additional quantity implicitly. Releasing a PN
   that has active quantity requires an explicit UI confirmation of intent (§8).
8. Movement history is immutable and append-only, and current state must be reconstructable from it (§15).
9. The QuantityFlow creation (carrying its initial `current_area_id` projection), the AssignedRoute snapshot and the
   Movement insert commit atomically or not at all (§13).
10. A duplicate release submission with the same `device_event_id` and the same normalized request returns the original
    result and creates nothing. The same `device_event_id` with a different normalized request is an idempotency
    conflict and creates nothing (§14).
11. Inactive entities (Area, Operation, RouteTemplate) never accept a release. The PartNumber master has no active or
    inactive lifecycle: the canonical PN string is always usable, with or without a master record.

---

## 5. WorkOrder and WorkOrderDemand Validation

### Work Order Number

- `work_order_number` is a **nullable**, arbitrary, opaque string (PROJECT_PROFILE §7 Work Order).
- When the user confirms saving with a blank Work Order Number, `NULL` is stored and the Work Order is an internal
  Work Order. The UI renders `—` (the placeholder is never persisted), labels the row as an internal Work Order without
  an external number, and allows the real number to be added later through an audited edit.
- Several Work Orders may hold `NULL` at the same time. Uniqueness applies to non-null numbers only (a partial unique
  index, §17). No temporary Work Order Number is ever generated.
- An entered Work Order Number is stored verbatim and never reformatted.
- Creating a Work Order with an existing (non-null) Work Order Number surfaces the existing Work Order instead of a
  duplicate. This duplicate handling applies only when a Work Order Number was entered.
- File import follows the same rule idempotently. Every imported row requires a Work Order Number, so Work Orders with a
  `NULL` number are created only manually.

### Dates

- `received_date` is required. When none is entered, it defaults to the current date on the site calendar (the time
  zone `SITE_TIMEZONE`), both for manual creation and for a Work Order created by file import.
- `work_orders.due_date` is nullable: a missing Work Order due date is valid data, not a validation error
  (PROJECT_PROFILE §8.2). It is only an entry default for the due dates of demand lines.

### Demand lines

- Each WorkOrderDemand requires:
  - a valid canonical `part_number`, normalized to uppercase with whitespace rejected (PROJECT_PROFILE §7). The
    PartNumber master metadata record is created on first valid use, but the demand row never depends on it;
  - `request_type IN ('NEW','MODIFY')`. Manual entry defaults to `NEW`; Scan Station intake defaults to `MODIFY`. Repair
    is a movement intent, never a request type;
  - `requested_quantity > 0`.
- `due_date` is nullable. A missing due date is valid data, never a validation error, and never blocks saving
  (PROJECT_PROFILE §8.3).
- `job_numbers` stores external Job Numbers as data: a list of arbitrary strings that is searchable, displayable and
  sortable, and never a domain aggregate.

### Canonical demand ordering key

Every consumer that orders demand uses the canonical demand ordering key (PROJECT_PROFILE §18):

1. `priority_rank` (Hot rank first);
2. then `due_date` ascending, with **NULLS LAST**;
3. then, for undated demand, the parent WorkOrder's `received_date` ascending;
4. then a stable, deterministic tie-breaker (creation order, that is the internal `id`).

This slice performs no such ordering itself. The consumers of the ordering (allocation, boards, priority) own their
supporting indexes.

### One line per Part Number on a Work Order

A canonical PN appears **at most once** among the current WorkOrderDemand rows of one WorkOrder. The same PN on the same
Work Order is one demand line whose quantity is edited, never a second line (GUI_DESIGN §11.2 and §11.3: the UI focuses
the existing line).

There is deliberately **no** unique index for this rule (§17 defines none). The Application layer enforces it and must
therefore serialize it against itself:

1. A save that adds demand lines takes the parent WorkOrder row lock.
2. After the lock is granted, it re-reads that Work Order's set of canonical PNs. Two concurrent saves that add the same
   PN therefore cannot both pass a snapshot taken before the lock.
3. The loser is refused with the ordinary duplicate-demand error and writes nothing.

The lock order stays demand (ascending id) before WorkOrder, the same order the demand-removal command takes, and the
release command never contradicts it.

### Priority rank (the Hot list)

- `priority_rank` is nullable and is the only storage of priority. A demand is **Hot** exactly when its rank is
  non-NULL, and rank 1 is the highest priority. There is no flag and no separate table.
- The ranked demands carry exactly the ranks `1..N`, **unique and dense** (enforced by §17).
- `priority_rank` has exactly two writers (IMPLEMENTATION_ROADMAP Phase 12): the Hot list command
  (`POST /api/hot-list/changes`) and the automatic or line-deletion removal in `app/application/hot_ranks.py`. Work
  Order create and edit reject the field as input.
- At every committed state, no ranked demand is inactive. A Hot entry whose demand becomes inactive (its Work Order is
  completed, or the line is fully allocated) is removed automatically, in the same transaction, by the allocation
  confirmation or the quantity-lowering Work Order save that caused it.
- Eligibility to join the list is an Application rule judged on the locked, re-read row: the demand exists, is not
  ranked, its Work Order is not completed, and it is **active** (PROJECT_PROFILE §14: `requested_quantity >
  allocated_quantity`). Each demand line is selected and ranked separately.
- A **Hot demand line is removed only with an explicit confirmation flag.** Without the flag, once every other removal
  rule passes, the removal answers 409, writes nothing and names the line's current rank. With the flag, the entry
  leaves the Hot list (dense renumbering, audited) and the line is deleted in one transaction, under the Hot advisory
  lock and the row locks, so it serializes with the Hot list command.
- The Hot list command, and the automatic removal inside a completing allocation, may also change the rank of a demand
  of a **completed** Work Order, with its audit row (§16).

### Editing demand

- Editing a WorkOrderDemand is permitted (Administrator and Manager, PROJECT_PROFILE §8.3). Edits are audited (§16) and
  never touch QuantityFlow or Movement.
- A line that has released production quantity keeps only a **restricted** edit (§8a; PROJECT_PROFILE §13; GUI_DESIGN
  §11). The Application layer enforces it, not only the UI:
  - `requested_quantity`, `due_date` and `job_numbers` stay editable;
  - `requested_quantity` may never fall below `max(released_quantity, allocated_quantity)`;
  - `request_type`, `requester`, `reason` and `notes` are refused (the `part_number` is not editable on any saved line);
  - an edit that carries the unchanged quantity is not judged, so a line allocated beyond its demand by an authorized
    correction keeps its other fields editable.
- Removal of such a line stays refused by PROJECT_PROFILE §13.

---

## 6. PartNumber Normalization, Creation, and Barcode

- Every PN entering the system is normalized first (PROJECT_PROFILE §7):
  1. leading and trailing whitespace is **trimmed**;
  2. after trimming, the value must be non-empty and contain **no internal whitespace** (space, tab, newline); such a
     value is rejected, never silently stripped;
  3. the value is canonicalized to **UPPERCASE**.

  `abc-123`, `ABC-123`, `AbC-123` and `" ABC-123 "` are all the canonical PN `ABC-123`; `"ABC 123"` and `"ABC\t123"`
  are invalid.
- A PartNumber master record is **created on first valid use** (PROJECT_PROFILE §8.1); no preloaded catalogue is
  required. The master is optional current metadata keyed by the canonical PN. Production rows never reference it by
  foreign key, so it can be hard-deleted (and later recreated for the same canonical PN) without touching production
  data (PROJECT_PROFILE §28).
- `POST /api/part-numbers` only creates. It takes the PN advisory lock, so every creator of a master record serializes
  per PN. `GET /api/part-numbers?search=` also matches the saved name. Management → Part Numbers edits the master,
  manages its image and hard-deletes it. Creation on first use is part of the same rules.
- The PN barcode carries the canonical PN itself: `PF:PN:<part-number>` (PROJECT_PROFILE §10). Because the PN is
  canonical uppercase, the barcode value is fully derived, and no separately stored, separately unique barcode key is
  needed for PNs. The barcode identifies only the PN and encodes no Work Order, quantity, route or location context.

---

## 7. Separation of Demand Save from Production Release

Demand save and production release are two distinct commands with distinct transaction boundaries:

- **Save demand** writes `work_orders` and `work_order_demands` only (and their audit rows, §16). It creates no
  QuantityFlow and no Movement and changes no projection. The UI labels demand as "business demand — separate from
  production".
- **Release to production** (§8) is never triggered implicitly by saving, importing or editing demand.

This preserves the canonical boundary: WorkOrder and WorkOrderDemand represent business demand and never define the
current production position; production release explicitly introduces physical quantity.

---

## 8. Explicit Release Command

**Input:** the Work Order and the WorkOrderDemand line the release is initiated from, the PN, the release quantity, the
Route Mode (`FLOATING` by default; `PLANNED` with a template selection), the confirmed starting Area and Operation, the
active-quantity confirmation flag, and the `device_event_id`. The command requires the demand line, and the PN must be
the line's own PN. The demand line is recorded as informational context for audit display and is the release evidence
of §8a; it never makes the demand own the Movement (§3). A `FLOATING` release takes no RouteTemplate, and a `PLANNED`
release requires one.

**Steps** (one transaction, §13):

1. **Validate.** The canonical PN is valid (normalized: trimmed, uppercase, no internal whitespace). The Area is active
   and configured as a starting Area. A **terminal** Area (`areas.is_terminal`, the Stockroom end of the flow,
   PROJECT_PROFILE §18) is never a starting Area; the Application layer refuses it, not only the release UI. The
   Operation is valid for that Area. The RouteTemplate is active **when the release is `PLANNED`**. The quantity is
   greater than 0 and within the initiating demand's remaining quantity (§8a). A demand line that is already fully
   allocated from stocked quantity needs no production and is refused (PROJECT_PROFILE §18). The PartNumber master has
   no active or inactive state, and its existence is never a release precondition.
2. **Confirm active quantity.** If the PN has active flows, the request must carry the explicit confirmation flag that
   the UI sets after showing the existing distribution; otherwise it is rejected. Nothing is ever auto-created or
   auto-merged.
3. **Snapshot the route.** The AssignedRoute is snapshotted **only for a `PLANNED` release** (§10); a `FLOATING` release
   creates none.
4. **Create the flow.** The QuantityFlow is created with its `route_mode`, its `assigned_route_id` (the snapshot's id
   for `PLANNED`, NULL for `FLOATING`, PROJECT_PROFILE §8.7) and its initial projection: `current_area_id` is the
   confirmed starting Area (§9, §15).
5. **Append the Movement.** The `RECEIVED` PartMovement is appended. For a `PLANNED` flow it references the snapshot's
   first step; for a `FLOATING` flow `assigned_route_step_id` stays NULL. It records the resolved Operation. Its
   `metadata` carries the request fingerprint (§14), the signed-in User who released it as `context.actor_user_id`, and
   the WorkOrderDemand context (§11).
6. **Commit and return** the flow id, the route mode, the route snapshot id when planned, the starting Area, the
   Operation, the quantity and the Movement id.

The release transaction appends **no** generic `audit_events` row: the `RECEIVED` Movement is itself the immutable
production audit record (§16).

Every command that can give a canonical PN active quantity or active demand takes one shared PN-level advisory lock,
and the release takes it before any row lock. Two concurrent unconfirmed releases of the same PN, or a release and a
Scan Station receipt of that PN, therefore cannot both pass the active-quantity check of step 2.

### 8a. Partial and repeated release of one demand

A WorkOrderDemand may be released in **several parts**, for example 20 of 50, then 12, then 18. Each part is an
independent release with its own `device_event_id`, its own QuantityFlow and its own `RECEIVED` Movement. From the
second part onward the PN has active quantity, so each part also needs its own explicit confirmation under §8 step 2.
Nothing is ever merged.

- **Released quantity is derived.** `released_quantity(demand)` is the sum of `RECEIVED.quantity` over the Movements
  whose `metadata.context.work_order_demand_id` is that demand (§11, §14). There is no stored counter and no column.
  Because `part_movements` is append-only, the value can never regress or drift and needs no reconciliation.
- **Remaining quantity is a hard cap.** `remaining_quantity = requested_quantity − released_quantity` is enforced on the
  server. A release beyond it, or any release once it reaches 0, is refused and creates nothing. The demand row is
  locked `FOR UPDATE` for the release transaction, so two concurrent partial releases of the same demand serialize and
  can never jointly over-release.
- **Restricted edit.** A demand with any released quantity keeps the restricted edit of PROJECT_PROFILE §13 (GUI_DESIGN
  §11):
  - the demand-edit command (§7) accepts `requested_quantity`, `due_date` and `job_numbers` for such a line and refuses
    `request_type`, `requester`, `reason` and `notes`;
  - `requested_quantity` may not fall below `max(released_quantity, allocated_quantity)`. An edit that carries the
    unchanged quantity is not judged, so a line allocated beyond its demand by an authorized correction keeps its other
    fields editable. Lowering the quantity below what is already released is therefore impossible, while raising it
    simply restores remaining quantity (and the Work Order's derived `OPEN`);
  - a refused `line_edits` entry writes nothing, not even the valid fields that travel with it, because one save is one
    transaction;
  - the edit command takes the SAME row lock as the release before it decides, and recomputes the released quantity
    under it. The two serialize in either arrival order: a release that commits first is seen by the edit (which then
    refuses a quantity below it), and an edit that commits first is seen by the release (which re-reads the row and caps
    on the new requested quantity). `released_quantity > requested_quantity` is therefore unreachable, not merely
    unlikely;
  - removal stays refused by PROJECT_PROFILE §13, and a line on the Hot list is removed only with the confirmation of
    §5;
  - the Work Order header and number edit is unaffected.
- **Read models.** The read models expose `released_quantity` and `remaining_quantity` per demand. A Work Order reads
  `RELEASED` only when **every** current demand line has `remaining_quantity = 0`. A partly released line keeps it
  `OPEN`, because its remainder can still be released. The stored `work_orders.status` stays `OPEN`; `RELEASED` and
  `COMPLETED` are derived read values (§17).

---

## 9. QuantityFlow Creation

- The columns follow PROJECT_PROFILE §8.7. A release writes:
  - `id`;
  - `part_number`, the canonical uppercase PN kept by the flow itself;
  - `quantity`;
  - `status`, `ACTIVE` on creation;
  - `route_mode`, `FLOATING` by default or `PLANNED`;
  - `assigned_route_id`, the nullable snapshot reference: the AssignedRoute's id exactly when `route_mode = 'PLANNED'`,
    NULL for `FLOATING`;
  - `created_at`, and `closed_at` NULL.
- The maintained projection columns are `current_area_id` and `current_machine_id`, plus `updated_at`.
  `current_area_id` is NOT NULL and is set by the INSERT itself to the confirmed starting Area, so a QuantityFlow row
  never exists without a valid current Area. A release leaves `current_machine_id` NULL; the Machine-processing
  Movements maintain it (§15).
- Lineage is not part of a release. It is recorded in the append-only `quantity_flow_lineage` edge table (parent to
  child, one edge per SPLIT child and per MERGED source), because a single self-reference column such as
  `parent_flow_id` cannot express a merge of N flows into one (§18).
- Within this slice a flow's quantity is immutable (the slice has no SPLIT, MERGED or `QUANTITY_ADJUSTED`), so
  conservation is verifiable as Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN). Beyond the slice, a
  flow's quantity still never changes outside its lineage: a `QUANTITY_ADJUSTED` addition introduces a NEW flow instead
  of editing an existing quantity.

---

## 10. AssignedRoute Snapshot Creation (`PLANNED` releases only)

- A `PLANNED` release copies the steps of the selected RouteTemplate into `assigned_routes` and `assigned_route_steps`:
  `sequence`, `area_id`, `operation_id`, `expected_duration`, `instructions` and `preferred_machine_id`. The split and
  merge copy of a snapshot copies the same fields. Merge compatibility ("structurally equal snapshots") includes
  `preferred_machine_id`, so flows whose snapshots differ only in a preferred Machine are never merged.
- A `FLOATING` release creates no snapshot; the flow's route trace is derived from Movement history (PROJECT_PROFILE
  §17).
- The snapshot references its source template (`source_route_template_id`, informational) but is independent: later
  template edits never alter it (PROJECT_PROFILE §8.10).
- The flow references its snapshot through `quantity_flows.assigned_route_id` (PROJECT_PROFILE §8.7), the only foreign
  key between the two tables; `assigned_routes` carries no reverse `quantity_flow_id` column. At most one flow
  references a snapshot (`UNIQUE (assigned_route_id)`), and the reference is present exactly when
  `route_mode = 'PLANNED'` (a CHECK, §17).
- A route deviation is recorded on the arrival Movement of the transfer, never on the snapshot. A snapshot's future
  steps change only through the audited Assigned Route adjustment (§16).
- The snapshot's first step must match the confirmed starting Area (and the Operation, where the step specifies one). A
  mismatch is a validation failure, not a silent adjustment.

---

## 11. RECEIVED PartMovement

The columns follow PROJECT_PROFILE §8.11. A release writes the `RECEIVED` Movement as follows:

- `movement_type = 'RECEIVED'`, the only type this slice produces. The movement-type check lists every current type
  (§17).
- `from_area_id` is NULL; `to_area_id` is the starting Area (NOT NULL); `operation_id` is the resolved starting
  Operation (NOT NULL, because a valid release always resolves or explicitly confirms one, §12).
- `quantity` is the flow quantity; the composite foreign key guarantees that the PN agrees with the flow.
- `assigned_route_step_id` references `assigned_route_steps.id`, a snapshot step, and never the mutable `route_steps`
  template row, so template edits can never alter the route context recorded by an existing Movement. A referenced step
  is a past step and never changes: the UPDATE-forbid trigger of §17 refuses every update, and the foreign key refuses
  its deletion.
  - The column is **nullable**. It is set for a `PLANNED` flow's `RECEIVED` (the snapshot's first step, which §10
    requires to match the confirmed starting Area) and NULL for a `FLOATING` flow, which has no AssignedRoute.
  - Its agreement with the flow is a **cross-table invariant** that no ordinary PostgreSQL CHECK can express: a
    `PLANNED` flow's `RECEIVED` must reference a step of **that flow's own** AssignedRoute, and a `FLOATING` flow's must
    stay NULL. The release transaction protocol (§13) enforces it, and reconciliation checks and tests verify it (§17).
  - The name is deliberately not a generic `route_step_id`, which would be ambiguous between `route_steps` and
    `assigned_route_steps`. It refines the illustrative `route_step_id` attribute of PROJECT_PROFILE §8.11.
- Columns a release leaves NULL, because a Management release involves no Scan Station, Worker or Machine:
  - `station_id` (the Scan Station of a station-recorded Movement, foreign key to `scan_stations.station_id`);
  - `worker_id` (the Worker identified at the station) and `scan_session_id` (the scanned Worker Session, foreign key to
    `worker_sessions`, the `ScanSession` of PROJECT_PROFILE);
  - `source_machine_id` and `destination_machine_id`;
  - `movement_reason` (the canonical column for the typed movement intent, whose only value `REPAIR` exists only on a
    `TRANSFERRED`, PROJECT_PROFILE §8.11), the free-text `reason`, and `reverses_movement_id`.
- `command_sequence` is 1: a release is a one-Movement command.
- `occurred_at` and `server_received_at`: see §14.
- `device_event_id` is NOT NULL and is the idempotency key (§14). It is unique together with `command_sequence`; because
  a release has exactly one Movement, one `device_event_id` names exactly one release.
- `metadata` carries the deterministic request fingerprint (§14). It records the signed-in User who released it as
  `context.actor_user_id` (derived by the server, never taken from the request; releases recorded before sign-in
  existed carry none) and the initiating WorkOrderDemand as `context.work_order_demand_id` for audit display. None of
  this creates ownership: WorkOrderDemand never owns Movement (§3).
- The row is immutable and insert-only. UPDATE and DELETE are revoked from the application role, and a raise-on-write
  trigger refuses them, following the rule that PostgreSQL constraints come first. The revocation is applied by
  `apply-grants` on the production stack; development and test databases keep one owner role (§17). This guard binds
  the application at all times. The Movement-history retention maintenance (PROJECT_PROFILE §28) is a separate,
  privileged Administrator path and is outside this slice.

---

## 12. Starting Area and Operation Resolution

- The starting Area comes from the Department and route configuration (PROJECT_PROFILE §22): the route snapshot's first
  step, with a default set by configuration (for example Material). The release UI confirms it; it is never guessed.
- The Operation is resolved from the starting Area's configuration. A single supported Operation resolves
  automatically. Several supported Operations require explicit confirmation in the release flow; the ambiguity blocks
  the write until it is confirmed.
- The Operation therefore exists in the schema before any Movement that carries one: `RECEIVED` records it from the
  start.

---

## 13. Transaction Boundary

One release submission is one database transaction:

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

- The idempotency check runs first, so a retry of a committed release returns the original result even if the entities
  have changed state since. It runs again after the PN advisory lock is granted (§8), so a concurrent identical retry
  that waited for the lock replays the original result instead of tripping over the quantity its own original made
  active.
- The QuantityFlow is inserted complete, with its initial `current_area_id` projection already set. No intermediate
  invalid or projection-less row ever exists (`current_area_id` is NOT NULL).
- Any failure rolls back everything. A Movement is never recorded without its flow and snapshot, and the reverse; no
  partial release state can commit.
- No generic `audit_events` row is written here: the `RECEIVED` Movement is the production audit record (§16).
- PartMovement remains the source of truth, and the projection remains rebuildable from it (§15).
- Demand save is a separate, earlier transaction (§7).
- No external side effects live inside the transaction.

---

## 14. Idempotency and Retry Behavior

### The release key and fingerprint

- The client generates one `device_event_id` (a UUID) per release submission and reuses it on every transport retry
  (timeout, connection reset, unknown outcome). The unique constraint on `(device_event_id, command_sequence)`
  guarantees at-most-once recording, because a release is a one-Movement command.
- A duplicate `device_event_id` is safe only when it carries the **same normalized request**. On submission the server
  computes a deterministic **request fingerprint**: a canonical hash over at least the PartNumber, the release quantity,
  the Route Mode (and the RouteTemplate when `PLANNED`), the starting Area, the Operation and the initiating
  WorkOrderDemand context. It stores the fingerprint in the `RECEIVED` Movement's `metadata` (§11). No separate
  idempotency table exists: the Movement row, found through its `device_event_id`, is the idempotency record.
- Replay outcomes:
  - same `device_event_id` and same fingerprint: return the original committed result unchanged and create nothing. The
    result is built from the immutable `RECEIVED` Movement, so it includes the release-time starting Area even after the
    flow has moved on;
  - same `device_event_id` and different fingerprint: return an explicit idempotency-conflict error and create nothing.
    A mismatch signals a client defect (an id wrongly reused for a new intent) and is never silently honoured.
- A **new** release intent gets a new `device_event_id`. It is then caught by the active-quantity confirmation rule
  (§8 step 2), so an accidental double release still requires explicit human confirmation.

### A replay by a different User

A Management command replayed by a different User than the one who recorded it is refused with 409
`recorded_by_another_user`, and nothing is written. This applies to the release, the Management allocation and its
reversal, the beyond-demand correction, the Hot list change and the Assigned Route adjustment. A record made before
sign-in existed, with no recorded User, counts as recorded by another User. The same User replaying from another
session or browser replays normally. The fingerprint is checked first, and identity is never part of a fingerprint.

### Idempotency of related Management commands

- **Beyond-demand correction.** The correction is a typed command that shares the `device_event_id` namespace of the
  allocations. Its fingerprint carries its own `command` value and never the acting identity.
- **Assigned Route adjustment.** The adjustment's audit row is its idempotency record. The UNIQUE partial index on its
  `device_event_id` (§17) makes it race-free, unlike the Hot list, which uses an advisory lock (§16). The fingerprint
  covers the flow, the future step ids read, the new steps and the reason, never the acting identity. A replay returns
  the recorded result from the audit row alone.

### Audit trail reader

`GET /api/tracking/audit-trail?part_number=` reads and writes nothing. For one canonical PN it lists:

- the `audit_events` rows of the PN's master record;
- the `audit_events` rows of the Work Orders and Work Order Demand lines that request the PN. A line deleted since is
  found through its `CREATED` row; a Work Order's header and completion rows are shared by every PN on the Work Order;
- the `audit_events` rows of the `AssignedRoute` rows of the PN's flows;
- the Management rows and every reversal in `work_order_allocations` on those demand lines, which are their own audit
  record.

Rows are ordered `(occurred_at DESC, source, id DESC)` and paged with a `(source, id)` cursor. The reader adds no index
and no schema object. Its lookups use `ix_audit_events_entity_type_entity_id_id`; the lookup of deleted lines can use
only the `entity_type` prefix of that index, so it scans the `WorkOrderDemand` audit rows of all event types (Hot rank
rows included), and the scope is recomputed on every page.

### Scope

- Idempotency in this slice is a property of the **release** command, because release is the one command here that
  introduces physical quantity and the one whose retry could duplicate it.
- Demand save (§7) deliberately carries no idempotency key. It writes business demand only: a repeated save creates no
  QuantityFlow and no Movement, an entered Work Order Number is unique (§5, partial unique index), and the only
  repeatable outcome is a second internal Work Order holding `NULL`, which §5 explicitly permits. Adding a key there
  would change the intake contract without protecting quantity integrity.
- File-based import needs no key either: the required, unique Work Order Number is its idempotency key, so a replayed
  file creates nothing new.

### Online semantics

`occurred_at` and `server_received_at` may both be assigned by the server and be equal. There is **no local offline
event queue**: a submission that cannot reach the server fails visibly and is blocked while disconnected (GUI_DESIGN
§3.6). `device_event_id` serves request idempotency today and remains compatible with any future, separately approved
offline design.

---

## 15. Derived Current-Position Projection

- `quantity_flows.current_area_id` and `quantity_flows.current_machine_id` are a maintained projection, a performance
  measure for hot read paths (Area inventory, boards).
- `current_area_id` is set by the QuantityFlow INSERT itself at release, in the same transaction as the `RECEIVED`
  Movement (§13). Later Movement types update the projection inside their own Movement transaction. For example,
  `AREA_COMPLETED` clears `current_machine_id` and keeps `current_area_id`: the finished quantity waits in its Area as
  `READY_TO_TRANSFER`.
- PartMovement history remains the source of truth. The value of `current_area_id` is defined as the `to_area_id` of
  the flow's latest effective Movement, and a replay procedure must be able to rebuild (or assert) every projection from
  Movement history alone.
- Nothing reads the projection as authority for a correctness-critical decision without holding the flow's row lock
  inside a transaction.

---

## 16. Audit Persistence Model

### What this slice audits

The slice audits (PROJECT_PROFILE §28):

- Work Order creation and edits, and WorkOrderDemand creation and edits: who, when and what changed;
- PN master creation (the barcode is derived from the canonical PN, §6, so nothing separate is issued);
- production release, audited by the `RECEIVED` PartMovement itself, the immutable production record. The releasing
  User (`context.actor_user_id`) and the WorkOrderDemand context live in its `metadata` (§11).

Historical records never disappear, and the history of demand edits must never rewrite prior values silently.

### Two mechanisms

Audit persistence has two complementary mechanisms with distinct responsibilities:

1. **PartMovement is the production audit record.** It is the sole source of truth for production state, replayable
   into projections (§15), and the sole audit record for production release and all production activity. One
   production action has one audit record: the release transaction writes **no** duplicate generic audit row (§13).
2. **One generic append-only table, `audit_events`, records master-data, business-demand and configuration changes
   only.** It covers WorkOrder, WorkOrderDemand and PartNumber; Worker, the environment configuration entities, Machine
   configuration, the application policy, Planned Routes, Users and Roles, and Scan Station devices; and the
   `ROUTE_ADJUSTED` correction of an AssignedRoute's future steps, which is the one route-guidance correction audited
   here. That correction moves no quantity, so it is never a Movement. The rows are descriptive history for display and
   accountability. They are **never** replayed to build state and never describe quantity movement. This is
   deliberately not an event-sourcing framework.

### `audit_events` columns

The constraints are listed in §17.

| Column | Meaning |
|---|---|
| `id` | `bigint` identity primary key; its order is the write order. |
| `event_type` | `CREATED`, `UPDATED`, `DELETED` (hard deletes of master and configuration records) or `ROUTE_ADJUSTED`. |
| `entity_type` | The audited entity: `WorkOrder`, `WorkOrderDemand`, `PartNumber`, `Worker`, `Department`, `Area`, `Operation`, `ScanStation`, `MachineAssetTagConfig`, `Machine`, `ApplicationPolicy`, `RouteTemplate`, `User`, `Role`, `ScanStationDevice` or `AssignedRoute`. |
| `entity_id` | The audited row's key, stored as text. It is polymorphic by design, so it has no foreign key. For `WorkOrder` and `WorkOrderDemand` it is the internal primary key; for `PartNumber` it is the canonical PN string (the master's natural key); the other entity types are described below. Integrity is guaranteed by writing the audit row in the same transaction as the audited change. |
| `actor_reference` | Nullable legacy text, kept for history and never backfilled. No converted writer writes it any more, so it is NULL on every new row. On older rows it holds an explicitly configured development or system actor identifier, or NULL. |
| `actor_user_id` | Nullable integer foreign key to `users (id)` (`fk_audit_events_actor_user_id_users`): the signed-in User, **derived by the server from the session principal only and never from a request body**. See "The acting User" below. |
| `occurred_at` | `timestamptz`. |
| `before_data` / `after_data` | `jsonb` snapshots of the audited fields; `before_data` is NULL for creation events. |
| `metadata` | `jsonb` for contextual detail. |

### Event mapping for this slice's entities

- Work Order create and edit: `CREATED` and `UPDATED` on `WorkOrder`.
- WorkOrderDemand create and edit: `CREATED` and `UPDATED` on `WorkOrderDemand`.
- PN master creation: `CREATED` on `PartNumber`, with the canonical PN as `entity_id`. PN master edit: `UPDATED`. Image
  change: `UPDATED` (recording the image digest). Hard delete: `DELETED`, with `before_data` holding the snapshot plus
  the image digest. The `PartNumber` snapshot is `{part_number, name, current_revision, erp_id}`.
- Production release maps to **no** `audit_events` row; its audit record is the `RECEIVED` Movement (§13).

### Hot list priority changes

- A priority change is audited as an `UPDATED` row on `WorkOrderDemand`, one row per demand whose `priority_rank`
  changed (including the demands renumbered to close a gap), written in the same transaction as the rank:
  - `before_data = {"priority_rank": old}` and `after_data = {"priority_rank": new}`, where NULL means off the list;
  - `metadata.hot_list_change` holds the command's `device_event_id`, its `action`, the request `fingerprint`, the
    row's `sequence` within the command, and an **identity snapshot** taken at command time (`work_order_demand_id`,
    `part_number`, `work_order_id`, `work_order_number`). The snapshot lets a replay of the command rebuild its
    `changes` from the audit rows alone, even after a demand was deleted.
- The audit row is also the command's idempotency record. Unlike the unique-constraint mechanism of §14, the lookup is
  made race-free by the Hot advisory lock.
- Rank changes made outside the command write the same rows. `metadata.hot_list_change` then carries:
  - `action` `AUTO_REMOVE` (an allocation or a quantity-lowering Work Order save made the demand inactive) or
    `LINE_DELETE` (the confirmed deletion of a Hot line);
  - the `sequence`, the identity snapshot, and a `cause` block (`trigger`, `reference`, and the `removed` list with each
    `reason`);
  - **no** `device_event_id` or `fingerprint` at that level, so the command's idempotency lookup never sees them.
- No audit vocabulary is added for priority.
- Rank writes on a demand of a completed Work Order are an accepted, documented exception to that Work Order being
  read-only (IMPLEMENTATION_ROADMAP Phase 12): a priority write is not a Work Order edit. Two writers make them: the
  automatic removal inside the completing allocation, and a manager's REMOVE or MOVE, through the command, of an entry
  left over from before that rule existed.

### File import intake marker

- A Work Order created by a file import carries `metadata = {"intake": {"channel": "FILE_IMPORT"}}` on its `WorkOrder`
  `CREATED` row only; its demand and Part Number rows are unchanged. A manually created Work Order records no `intake`
  key.
- An import save that changes an existing Work Order carries the same marker on each `WorkOrderDemand` `UPDATED` or
  `CREATED` row it appends, and writes no `WorkOrder` header row. The Part Number `CREATED`, completion and Hot rank
  rows it may cause keep their own metadata and no `intake` key.
- The marker needs no column, index or CHECK. The import run, its batch and the file name are not stored.

### Worker configuration

- Workers are audited with `entity_type` `Worker` (configuration of the Scan Station audit identity, never production
  activity). The `DELETED` event type is used for hard deletes of master and configuration records; its first writer
  is the Part Number hard delete.
- Every effective Worker write appends exactly one row in the same transaction as the write. `entity_id` is the Worker's
  internal id as text, `actor_reference` is NULL and `metadata` is NULL.
- A profile row snapshots `{name, badge_barcode, is_active}` (`before_data` NULL for `CREATED`). An avatar row snapshots
  `{"avatar": null | {content_type, byte_size, sha256}}`: a digest, never image bytes.
- The Worker row is locked first, so within each facet (profile, avatar) a row's `before_data` is the previous row's
  `after_data`. The two facets interleave and are not chained to each other.
- Rejected writes and no-ops append nothing.

### Environment configuration

- The environment writes are audited with five `entity_type` values: `Department`, `Area`, `Operation`, `ScanStation`
  and `MachineAssetTagConfig` (the Asset Tag format).
- `entity_id` is the internal id as text for Department, Area and Operation; the Station ID for `ScanStation`; and
  `'1'` for the singleton `MachineAssetTagConfig`.
- Every effective create or update appends exactly one `CREATED` or `UPDATED` row in the same transaction. A multi-field
  PATCH is one row; a no-op, a rejected write or a lost race appends nothing.
- The snapshots are explicit field lists:
  - Department: `{name, is_active, board_seconds_per_row, board_min_page_seconds}`;
  - Area: `{department_id, name, barcode_value, description, color, icon_url, is_terminal, is_active,
    worker_identification_mode, fixed_worker_id}`, plus `worker_session_timeout_minutes` (the per-Area override, `null`
    when unset);
  - Operation: `{area_id, code, name, description, default_expected_duration_seconds, is_external, is_active}`, with the
    duration in seconds as a JSON number and `null` when unset;
  - ScanStation: `{area_id, is_active}`;
  - MachineAssetTagConfig: `{prefix, digits}`. `next_sequence` (the never-reuse counter of Machine creation) is not
    configuration and is never audited.
- A column added later is audited only when it is added to its entity's snapshot.
- Every update locks its row first, in the mode its own UPDATE takes, and snapshots under the lock. Consecutive rows of
  one entity therefore chain: each `before_data` equals the previous `after_data` on every key present in both.
- There is no backfill. The first audited row of a configuration that existed before auditing began is an `UPDATED`
  whose `before_data` holds the state found.
- The Scan Station theme preference (`scan_stations.theme_preference`) is a display preference, not configuration. It is
  never audited and never enters the `ScanStation` snapshot.

### Machine configuration

- Machine configuration writes are audited with `entity_type` `Machine`; `entity_id` is the Machine's internal id as
  text.
- Every effective create, metadata or maintenance-context edit, maintenance start or maintenance clear appends exactly
  one `CREATED` or `UPDATED` row in the same transaction.
- The snapshot is the explicit key list `{area_id, name, asset_tag, description, manufacturer, model, serial_number,
  installed_on, notes, maintenance_since, maintenance_note, maintenance_expected_return}`. Dates are ISO-8601 text, and
  `maintenance_since` is a UTC ISO-8601 instant, so the text never depends on the connection time zone.
- Excluded keys: `id` (it is the `entity_id`); `retired_on` (owned by `machine_lifecycle_events`, so no transition is
  recorded twice); `state_changed_at` (the age of the runtime state, moved by production commands); `created_at` and
  `updated_at`.
- Retirement and reactivation stay recorded only in `machine_lifecycle_events`. A plain retirement or reactivation
  appends no audit row. The Save draft that a retirement applies, or the rename, Area move or maintenance clear of a
  reactivation, appends one `UPDATED` row whose `metadata` is `{"machine_lifecycle_event_id": <id>}`.
- Every Machine write locks the Machine row first (`FOR NO KEY UPDATE`; retirement keeps `FOR UPDATE`; creation takes no
  lock on `machines`), so consecutive rows of one Machine chain as for the environment entities.
- There is no backfill.

### Application policy, passwords and sign-in

- Global policy writes are audited with `entity_type` `ApplicationPolicy`. `entity_id` is the Administration section:
  `worker-sessions` (the Worker session timeout), `correction-permissions` (the Undo reason policy), `due-soon` (the Due
  Soon warning panel of Settings), `data-retention` (History archival & purge) and `sign-in` (the Settings → User
  sign-in panel).
- Every effective `PUT` on one of these sections appends exactly one `UPDATED` row in the same transaction; a `PUT`
  that changes nothing appends nothing. The snapshots are:

  | Endpoint | Snapshot |
  |---|---|
  | `PUT /api/policies/worker-sessions` | `{worker_session_timeout_minutes, badge_confirm_done, badge_confirm_queue, badge_confirm_undo}` |
  | `PUT /api/policies/correction-permissions` | `{undo_reason_required}` |
  | `PUT /api/policies/due-soon` | `{due_soon_min_days, due_soon_lead_time_percent, due_soon_max_days}` |
  | `PUT /api/policies/data-retention` (setting or clearing the period) | `{retention_period_months}` |
  | `PUT /api/policies/sign-in` | `{user_session_expires, user_session_days, sign_in_lockout_attempts, sign_in_lockout_minutes, require_password_change}` |

  The `sign-in` row carries `actor_user_id` set to the signed-in User.
- Password writes append `UPDATED` rows for entity type `User` with the snapshot `{password_set, password_temporary,
  password_changed_at}` (`password_changed_at` as ISO-8601 text; never a hash, salt, token or password) and the
  metadata key `password_change` (`SET_BY_ADMINISTRATOR`, `CHANGED_BY_USER` or `RECOVERY_CLI`; the recovery command
  also records `source: cli`).
- The first Administrator created by first-run setup appends the `User` `CREATED` row and the password `UPDATED` row
  with `source: first-run-setup` and `actor_user_id` NULL.
- Sign-ins, sign-outs, failed attempts, locks and session ends are logged, never audited.
- Worker Session rows and their closes, gate sign-ins included, are production audit identity (PROJECT_PROFILE §9
  ScanSession and §19 Worker Sessions; `worker_sessions`, `scan_session_id`), never `audit_events` rows.

### Planned Routes

- Planned Routes configuration writes are audited with `entity_type` `RouteTemplate`; `entity_id` is the template's
  internal id as text.
- Every effective write appends exactly one row in the same transaction: `CREATED` (create), `UPDATED` (an edit or an
  archive) or `DELETED` (the delete of a never-used template). A no-op or rejected write appends nothing.
- The snapshot is the explicit key list `{name, description, archived_at, steps}`, with `archived_at` as UTC ISO-8601
  text and each step as `{sequence, area_id, operation_id, expected_duration_seconds, preferred_machine_id,
  instructions}` (the duration in seconds as a JSON number).
- Assigned Route snapshots and releases are never audited as `RouteTemplate`. Only a snapshot's adjustment is audited,
  as `AssignedRoute` (below).

### Users and roles

- Users and roles are audited with `entity_type` `User` and `Role`. `entity_id` is the internal id as text,
  `actor_reference` stays NULL and `actor_user_id` carries the signed-in User.
- Every effective write appends exactly one row in the same transaction:
  - a role create is `CREATED` with `before_data` NULL and `after_data` `{name, permissions}` (the permission keys
    sorted by value);
  - a role rename, or a grant or revoke delta, is one `UPDATED` row with the same snapshot before and after;
  - a user create is `CREATED` with the profile snapshot `{login_name, display_name, role_id, is_active}`;
  - a user profile edit is one `UPDATED` row with that snapshot before and after;
  - a user avatar change is an `UPDATED` row with `{"avatar": null | digest}`: a digest, never image bytes, as for
    Workers.
- Every role and user write first takes the `partflow:user-administration` advisory lock and then re-reads the acting
  user's permissions. The permission-management guard and the holder counts are plain reads under that lock. The role
  or user row is locked next, so consecutive rows of one entity chain.
- The three seeded roles have no `CREATED` row; the first edit's `before_data` is the seed.
- The User theme preference (`users.theme_preference`) is a display preference written only by the User's own toggle
  (`PUT /api/session/theme-preference`). It is never audited, never in the `User` snapshot, and does not change
  `users.updated_at`. Its write locks only the User row (`FOR NO KEY UPDATE`) and does not take the user-administration
  advisory lock, because it changes no grant, role, credential or activity.
- A no-op or rejected write appends nothing.

### Scan Station devices

- Station device enrollment is audited with `entity_type` `ScanStationDevice`; `entity_id` is the internal device id as
  text.
- Issuing an enrollment code appends a `CREATED` row carrying the issuing User's `actor_user_id`.
- The station's own activation appends an `UPDATED` row with no User (`actor_user_id` NULL, `metadata.source =
  "station-activation"`). Activating a device that replaces another appends one more `UPDATED` row for the replaced
  device (`metadata.replaced_by_device_id`).
- A revocation appends an `UPDATED` row carrying the revoking User.
- A code, a token or a digest is never audited, and `last_seen_at` is device-contact metadata that is not audited.
- Scan Station commands read the grants of the role applied at Scan Stations with a plain read and never lock `users`,
  `roles` or `role_permissions`.

### Assigned Route adjustment

- The authorized adjustment of a `PLANNED` flow's future steps is audited with the event type `ROUTE_ADJUSTED` and
  `entity_type` `AssignedRoute`; `entity_id` is `assigned_routes.id` as text.
- Every committed adjustment appends exactly one row in the same transaction; a refusal or an idempotent replay appends
  nothing.
- `before_data` and `after_data` are `{steps: [{id, sequence, area_id, operation_id, expected_duration_seconds,
  preferred_machine_id, instructions}]}` for the whole route, past steps included, with the step ids. Movements and
  deviation metadata name steps by id, so the audit must resolve them, and the steps an adjustment deletes survive only
  here.
- `metadata.route_adjustment` is `{device_event_id, fingerprint, quantity_flow_id, part_number, reason,
  kept_through_sequence}`, and `actor_user_id` carries the signed-in User.
- The lock order is: the Part Number advisory lock; then the `quantity_flows` row `FOR UPDATE`; then Machines, Areas and
  Operations `FOR KEY SHARE` in ascending order; then the step DELETE and INSERT and the audit INSERT.
- No `quantity_flows` column, Movement, Route Template or other snapshot is written.

### Allocation rows as their own audit record

The beyond-demand correction writes one append-only `work_order_allocations` row (with `exceeds_demand`, the mandatory
reason, `actor_user_id`, and the before and requested figures in its command metadata) and no `audit_events` row. The
Hot list removals it triggers are audited as described above.

### The acting User

- The writers that fill `actor_user_id`:
  - the password writes and the user sign-in policy writes;
  - every Administration configuration write: Departments, Areas, Operations, Scan Stations, the Asset Tag format,
    Workers, the policy sections, Roles and Users;
  - every Management write: Machines including their lifecycle events, Planned Routes, Part Numbers, Work Orders and
    demand, the Hot list, Management allocations and reversals, and the Work Order completion and Hot removal rows those
    writes cause.
- Rows written by Scan Station commands (receipts, station allocations and their Hot removals, and the Part Number and
  Work Order creation of a station intake) keep `actor_user_id` NULL. Legacy rows keep it NULL too.
- `machine_lifecycle_events` and `work_order_allocations` have the same nullable `actor_user_id` foreign key
  (`fk_machine_lifecycle_events_actor_user_id_users`, `fk_work_order_allocations_actor_user_id_users`). Their legacy
  text column `machine_lifecycle_events.actor` (once taken from the Machine retire and reactivate request body, which no
  longer carries it) is kept for history, written no more and never backfilled.
- Through these foreign keys, every Management command and every audited configuration write takes a `FOR KEY SHARE`
  lock on the acting User's `users` row at the INSERT. It conflicts only with a login-name rename of that User and
  forms no cycle with the other locks (including the user-administration lock above).
  - During such a rename a Management command waits **while holding its advisory locks**: its PN lock and, for an
    allocation, a Hot list change, or a Work Order quantity edit or confirmed line delete, the Hot list lock. Commands
    queuing on those locks (every station allocation, receipts and reversals of that PN, Hot list changes, Work Order
    saves that take the Hot lock) wait until the rename commits. The wait is bounded by the rename's short transaction,
    and there is no cycle.
  - An audited configuration write waits the same way, only behind a login-name rename of the acting User.
- Scan Station commands never read or lock `users`, `roles`, `role_permissions`, `user_credentials` or
  `user_sessions`. Production commands never read or lock `users`, `roles` or `role_permissions`.

### Rules

- Every audit row commits in the **same transaction** as the change it records. An audited write without its audit row,
  or an audit row without its write, must be impossible.
- Audit rows are append-only. UPDATE and DELETE are revoked from the application role, and a raise-on-write trigger
  refuses them, exactly as for `part_movements`. The trigger exists since the table was created; the application-role
  revocation is applied by `apply-grants` on the production stack, where the backend connects as `partflow_app` (§17).
  The development and test databases keep one owner role.
- An edit appends a new `UPDATED` row; prior rows are never rewritten.

---

## 17. Database Constraints and Indexes

This section lists the current tables, columns, constraints and indexes that the slice uses or that the documents cite
here. The migration that added a column or constraint after the table was created is named in parentheses where it
helps to find it.

### Configuration tables

**`departments`**: PK `id`; `UNIQUE (name)`; `is_active`; `created_at`, `updated_at`. The Production Board rotation
timing is configured per Department in whole seconds (`0025_phase13_display_settings`):
`board_seconds_per_row integer NOT NULL DEFAULT 3` with `ck_departments_board_seconds_per_row_range` (1–60), and
`board_min_page_seconds integer NOT NULL DEFAULT 6` with `ck_departments_board_min_page_seconds_range` (1–300).

**`areas`**: PK `id`; `department_id NOT NULL` FK; `name NOT NULL`; `barcode_value` nullable with `UNIQUE
(barcode_value)` where assigned and the `PF:AREA:` namespace check `ck_areas_barcode_value_namespace` (the barcode is
assigned once by the server and guarded by the trigger `trg_areas_forbid_barcode_change`); `description`, `color`,
`icon_url` nullable; `is_terminal boolean NOT NULL DEFAULT false`; `is_active`; `created_at`, `updated_at`. The
environment-setup migration `0003_phase35_environment` added the display columns, `is_terminal` and the barcode
guard. Further columns:

- `worker_identification_mode text NOT NULL DEFAULT 'DISABLED'` (`ck_areas_worker_identification_mode`: `DISABLED`,
  `FIXED` or `SCANNED`) and `fixed_worker_id` (nullable FK `fk_areas_fixed_worker_id_workers`;
  `ck_areas_fixed_worker_shape`: a Fixed Worker exists exactly in `FIXED` mode), added by
  `0019_phase13_worker_identity`. The same migration added the nullable foreign keys `part_movements.worker_id` and
  `work_order_allocations.allocated_by_worker_id`, each with a CHECK that a Worker requires a `station_id`
  (`ck_part_movements_worker_requires_station`, `ck_work_order_allocations_worker_requires_station`), with no index, no
  backfill and a refusing downgrade.
- `worker_session_timeout_minutes integer`, the nullable per-Area override (`ck_areas_worker_session_timeout_range`:
  NULL or 1–720), added by `0020_phase13_worker_sessions`.

No `machine_assignment_mode` column exists: assignment behaviour follows from the Area's Machines (PROJECT_PROFILE
§12).

**`operations`**: PK `id`; `area_id NOT NULL` FK; `code NOT NULL`, `UNIQUE (area_id, code)`; `name`; `description`
nullable; `default_expected_duration` nullable; `is_external boolean NOT NULL DEFAULT false`; `is_active`;
`created_at`, `updated_at`.

### Part Number master

**`part_numbers`**: the optional current-metadata master.

- PK `part_number text`: the canonical uppercase PN, a natural key with no surrogate id.
- `CHECK (part_number = upper(part_number COLLATE "C") AND part_number COLLATE "C" !~ '[[:space:]]' AND part_number <>
  '')` enforces the canonical form. Both clauses run under the `"C"` collation, so they check ASCII only and are
  independent of the operating system's C library (`0018_phase13_pn_check_collation`). The full Unicode uppercase and
  the refusal of every Unicode whitespace are owned by the domain normalization (§6).
- `created_at`, `updated_at`. There is no `is_active`: the PN master has no active or inactive lifecycle.
- The nullable details `name`, `current_revision` and `erp_id` (free text, not unique, no index), and the image on the
  row: `image` (`bytea`), `image_type`, `image_updated_at`, with `ck_part_numbers_image_shape` (all three or none),
  `ck_part_numbers_image_type` (`image/png`, `image/jpeg`, `image/webp`) and `ck_part_numbers_image_size` (1 byte to
  2 MiB) (`0023_phase13_part_number_master`).
- The PN barcode is derived (`'PF:PN:' || part_number`, PROJECT_PROFILE §10). There is no stored `barcode_value` column
  and there are no `lower(...)` expression indexes; canonical uppercase makes them unnecessary.
- No production table references this table by foreign key, so a master row can be hard-deleted (and recreated later
  for the same canonical PN) per PROJECT_PROFILE §28.

### Business demand tables

**`work_orders`**:

- PK `id`, the stable internal identity and never the user-facing identifier.
- `work_order_number` **nullable**, with the **partial unique index** `uq_work_orders_work_order_number`
  (`UNIQUE (work_order_number) WHERE work_order_number IS NOT NULL`), so many internal Work Orders may hold `NULL` while
  non-null numbers stay unique (§5).
- `received_date NOT NULL`; `due_date` nullable (a missing Work Order due date is valid data, §5).
- `status text NOT NULL DEFAULT 'OPEN'`. The stored value is `OPEN`; `RELEASED` (§8a) and `COMPLETED` are derived read
  values and are never stored.
- `completed_at`, nullable: the canonical done-date column (PROJECT_PROFILE §8.2). It is derived from the allocation
  events and never entered by hand: a maintained projection of the allocation rows. The partial index
  `ix_work_orders_completed_at_id` on `(completed_at, id) WHERE completed_at IS NOT NULL` serves the keyset-paged
  completed history.
- `created_at`, `updated_at`.

**`work_order_demands`**:

- PK `id`; FK `work_order_id NOT NULL`.
- `part_number text NOT NULL`: the canonical uppercase PN kept by the demand, with the same canonical-form CHECK as
  `part_numbers` and **no foreign key** to the master.
- `request_type NOT NULL CHECK (request_type IN ('NEW','MODIFY'))`.
- `requested_quantity int NOT NULL CHECK (requested_quantity > 0)`.
- `allocated_quantity int NOT NULL DEFAULT 0 CHECK (allocated_quantity >= 0)`: a maintained projection of the demand's
  active allocation rows.
- `due_date` nullable. A missing due date is valid data (§5), never required by a validation rule or a constraint;
  undated demand orders after dated demand by the canonical demand ordering key (§5).
- `priority_rank` nullable, with `CHECK (priority_rank IS NULL OR priority_rank >= 1)`
  (`ck_work_order_demands_priority_rank_positive`) and the non-deferrable `UNIQUE (priority_rank)`
  (`uq_work_order_demands_priority_rank`; NULLs stay distinct). The Hot list command and the automatic or line-deletion
  removal both write in two flushes, clear and then assign, so neither produces a transient duplicate. The migration
  that added both constraints (`0013_phase12_priority`) first runs a refusing pre-check that leaves the database
  unchanged when the existing ranks are not exactly `1..N`.
- `job_numbers text[] NOT NULL DEFAULT '{}'`: arbitrary external strings preserved verbatim; an empty list is valid.
  It is metadata only: no `Job` aggregate, no foreign key, and no GIN index.
- `requester`, `reason`, `notes` nullable; `created_at`, `updated_at`.
- Indexes `ix_work_order_demands_work_order_id` on `(work_order_id)` and `ix_work_order_demands_part_number` on
  `(part_number)`.

### Route tables

**`route_templates`**: PK `id`; `name NOT NULL`; `description` nullable; `archived_at timestamptz` nullable (`NULL`
means active; an archived template is never offered for new route assignments); `created_at`, `updated_at`. There is
**no `version` column and no template-versioning framework** (PROJECT_PROFILE §8.8): the existing `assigned_routes`
snapshots preserve historical route definitions. A template ever referenced by an `assigned_routes` row is archived
instead of deleted; a hard `DELETE` is legitimate only for a never-referenced template.

**`route_steps`**: PK `id`; `route_template_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (route_template_id,
sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable; `expected_duration` nullable; `instructions` nullable;
`preferred_machine_id`, the canonical preferred-Machine reference (PROJECT_PROFILE §8.9), a nullable FK
`fk_route_steps_preferred_machine_id_machines` to `machines.id` (NO ACTION, because Machines are retired, never
deleted; `0024_phase13_planned_routes`). The match between the preferred Machine and the step's Area is an Application
rule checked at save time only, so a stored preference may become stale and is then shown `(unavailable)`.

**`assigned_routes`**: PK `id`; `source_route_template_id` FK nullable (informational; indexed by
`ix_assigned_routes_source_route_template_id`, which serves the Planned Routes usage list, the ever-used check and the
foreign-key check of a template delete); `created_at` (the snapshot time). The snapshot carries **no**
`quantity_flow_id` back-reference: the owning flow references it through `quantity_flows.assigned_route_id`
(PROJECT_PROFILE §8.7), the single foreign key between the two tables.

**`assigned_route_steps`**: PK `id`; `assigned_route_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (assigned_route_id,
sequence)`; `area_id NOT NULL` FK; `operation_id` FK nullable; `expected_duration` nullable; `instructions` nullable;
`preferred_machine_id` nullable. These are snapshot copies of the template step fields (§10).

- `preferred_machine_id` is a plain integer **without a foreign key**. Every writer validates it: it is copied from an
  FK-checked template column (or from another snapshot), or written by the `ROUTE_ADJUSTED` command after
  `route_templates.lock_step_references` checks it under `FOR KEY SHARE`; and Machines are never deleted. A foreign key
  would make release, receipt, split and merge take Machine row locks after an Area or Machine lock, against the
  production lock order.
- Past steps are immutable in the database (`0032_phase14_route_adjusted`). The statement-level trigger
  `trg_assigned_route_steps_forbid_update` (function `partflow_assigned_route_steps_forbid_update`) refuses every
  UPDATE, and a step that a Movement references is never deleted (the `part_movements.assigned_route_step_id` foreign
  key is `NO ACTION`). DELETE stays allowed for the unreferenced future steps an adjustment replaces.

### Production tables

**`quantity_flows`**:

- PK `id`.
- `part_number text NOT NULL`: the canonical uppercase PN kept by the flow, with the same canonical-form CHECK and **no
  foreign key** to the master.
- `quantity int NOT NULL CHECK (quantity > 0)`.
- `status NOT NULL DEFAULT 'ACTIVE'` with `ck_quantity_flows_status` (`ACTIVE`, `SPLIT`, `MERGED`, `SCRAPPED`,
  `REVERSED`, `STOCKED`) and `ck_quantity_flows_status_closed_at` (`closed_at` is set exactly when the status is not
  `ACTIVE`).
- `route_mode NOT NULL DEFAULT 'FLOATING' CHECK (route_mode IN ('FLOATING','PLANNED'))`.
- `assigned_route_id` FK nullable to `assigned_routes (id)`, with `UNIQUE (assigned_route_id)` (at most one flow per
  snapshot) and `CHECK ((route_mode = 'PLANNED') = (assigned_route_id IS NOT NULL))` (a `PLANNED` flow always
  references its snapshot and a `FLOATING` flow never does, PROJECT_PROFILE §8.7).
- `current_area_id NOT NULL` FK (set at INSERT, §13); `current_machine_id` FK nullable to `machines.id`.
- `UNIQUE (id, part_number)`, the target of the composite foreign key.
- `created_at`, `updated_at`, `closed_at` nullable.
- Indexes: `(part_number) WHERE status = 'ACTIVE'`, `(current_area_id)` and `(current_machine_id)`.
- No lineage column exists on this table; lineage lives in the `quantity_flow_lineage` edge table (§9, §18).

**`part_movements`**:

- PK `id`, a `bigint` identity whose order is the event order.
- `quantity_flow_id NOT NULL` and `part_number text NOT NULL` (the canonical uppercase PN kept by the Movement, so the
  history identifies its PN without any join to the master), with the composite FK `(quantity_flow_id, part_number)`
  to `quantity_flows (id, part_number)`.
- `movement_type NOT NULL` with `ck_part_movements_movement_type`, which admits `RECEIVED`, `TRANSFERRED`,
  `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`, `SCRAPPED`, `QUANTITY_ADJUSTED`,
  `REVERSED` and `STOCKED`. The list widens additively.
- `quantity int NOT NULL CHECK (quantity > 0)`.
- `from_area_id` FK nullable; `to_area_id NOT NULL` FK.
- The per-type shape check `ck_part_movements_movement_shape`. Its `RECEIVED` branch requires `from_area_id IS NULL`
  and no source or destination Machine; it does not constrain `station_id`, so a Scan Station receipt may record its
  station while a Management release leaves it NULL.
- `operation_id NOT NULL` FK.
- `assigned_route_step_id` FK nullable to `assigned_route_steps (id)` (set for `PLANNED` flows, NULL for `FLOATING`
  flows, §11).
- `station_id` FK nullable to `scan_stations.station_id`; `source_machine_id` and `destination_machine_id` FKs nullable
  to `machines.id`; `worker_id` FK nullable to `workers.id`; `scan_session_id` nullable (below).
- `occurred_at timestamptz NOT NULL`; `server_received_at timestamptz NOT NULL`.
- `device_event_id NOT NULL` and `command_sequence integer NOT NULL DEFAULT 1` (`command_sequence >= 1`), with the
  unique constraint `uq_part_movements_device_event_id_command_sequence` on `(device_event_id, command_sequence)`. One
  `device_event_id` identifies one application command, which may append several Movements; a release appends one.
- `movement_reason`, `reason` and `reverses_movement_id` (FK to `part_movements`, `UNIQUE`), with their checks
  `ck_part_movements_movement_reason`, `ck_part_movements_reason_required` and `ck_part_movements_reverses_shape`.
- `metadata jsonb`.
- Index `(quantity_flow_id, id)`.
- The **partial expression index** `ix_part_movements_received_demand_context` on the release-evidence path:
  `((metadata['context'] ->> 'work_order_demand_id')::int) WHERE movement_type = 'RECEIVED'`. It exists because
  `released_quantity` is derived from that metadata context on every Work Order read (§8a), and the history is
  append-only and retained for years (PROJECT_PROFILE §28), so without it the hot read path would scan the whole table.
  The stored expression must stay identical to the one the Application layer emits (the JSONB **subscript** form; the
  `->` operator form is a different expression node and would not match). The index adds **no** column, no foreign key
  and no stored counter; the derivation stays the single source of truth.
- `ix_part_movements_part_number_occurred_at_id` on `(part_number, occurred_at, id)`, which serves the per-PN Movement
  history of Tracking in time order with keyset paging (`0012_phase11_tracking_index`).
- `ix_part_movements_assigned_route_step_id` on `(assigned_route_step_id)`, which serves the foreign key's check when an
  adjustment deletes an unreferenced future step and the edit's "is this step referenced" boundary.
- Immutability guard: UPDATE and DELETE revoked, plus the raise trigger `trg_part_movements_forbid_mutation`.

**`part_movements.scan_session_id`** (`0020_phase13_worker_sessions`): `bigint` nullable (history keeps NULL and is
never backfilled); `ck_part_movements_session_requires_worker` (`scan_session_id IS NULL OR worker_id IS NOT NULL`) and
the composite FK `fk_part_movements_scan_session_worker_sessions` `(scan_session_id, worker_id, station_id)` to
`worker_sessions (id, worker_id, station_id)`, so a Movement can never name another Worker's or another station's
session. There is no index, because no reader filters by it. The upgrade validates the CHECK and the foreign key with
one scan of `part_movements`; the downgrade refuses while any session, Area override or non-default timeout exists.

### Audit table

**`audit_events`**:

- PK `id`, a `bigint` identity.
- `event_type NOT NULL` with `ck_audit_events_event_type`: `CREATED`, `UPDATED`, `DELETED`, `ROUTE_ADJUSTED`.
- `entity_type NOT NULL` with `ck_audit_events_entity_type`: `WorkOrder`, `WorkOrderDemand`, `PartNumber`, `Worker`,
  `Department`, `Area`, `Operation`, `ScanStation`, `MachineAssetTagConfig`, `Machine`, `ApplicationPolicy`,
  `RouteTemplate`, `User`, `Role`, `ScanStationDevice`, `AssignedRoute`. Both lists widen additively.
- `entity_id NOT NULL` (no foreign key: polymorphic, §16).
- `actor_reference` nullable; `actor_user_id` nullable FK `fk_audit_events_actor_user_id_users` to `users (id)`.
- `occurred_at timestamptz NOT NULL`; `before_data jsonb` nullable; `after_data jsonb` nullable; `metadata jsonb`.
- Index `ix_audit_events_entity_type_entity_id_id` on `(entity_type, entity_id, id)`.
- The partial expression index `ix_audit_events_hot_list_device_event_id` on `(metadata['hot_list_change'] ->>
  'device_event_id') WHERE entity_type = 'WorkOrderDemand'`, for the Hot list command's idempotency lookup. The stored
  form is the explicit `->>` operator over the JSONB subscript, and the Application lookup emits the same expression.
- The UNIQUE partial expression index `uq_audit_events_route_adjustment_device_event_id` on
  `(metadata['route_adjustment'] ->> 'device_event_id') WHERE entity_type = 'AssignedRoute'` (the JSONB subscript
  form, created as raw SQL so the stored expression is the one the Application emits). It makes the adjustment's
  idempotency race-free.
- Append-only guard: UPDATE and DELETE revoked, plus the raise trigger `trg_audit_events_forbid_mutation` (§16).

### Policy and Worker Session tables

**`application_policy`** (`0020_phase13_worker_sessions`) is the one typed singleton for global policies:

- PK `id` with `ck_application_policy_singleton` (`id = 1`), seeded by the migration so the row always exists;
  `created_at`, `updated_at`.
- `worker_session_timeout_minutes integer NOT NULL DEFAULT 15` with
  `ck_application_policy_worker_session_timeout_range` (1–720).
- `badge_confirm_done`, `badge_confirm_queue` and `badge_confirm_undo` (`0021_phase13_badge_confirmation`):
  `boolean NOT NULL DEFAULT true`, one per sensitive action, deciding the form of its final gate in Scanned-session
  Areas.
- `undo_reason_required` (`0022_phase13_undo_reason_policy`): `boolean NOT NULL DEFAULT false`. While it is true, the
  Undo command refuses a reversal without a reason.
- `due_soon_min_days` (default 2, `ck_application_policy_due_soon_min_days_range`, 0–365),
  `due_soon_lead_time_percent` (default 15, `ck_application_policy_due_soon_lead_time_percent_range`, 1–100) and
  `due_soon_max_days` (default 7, `ck_application_policy_due_soon_max_days_range`, 0–365), with
  `ck_application_policy_due_soon_window_order` (`due_soon_min_days <= due_soon_max_days`)
  (`0025_phase13_display_settings`). They are `integer NOT NULL` and hold the one global Due Soon warning policy.
- `retention_period_months integer NULL` with `ck_application_policy_retention_period_range` (NULL or 12–1200)
  (`0027_phase13_retention_period`): the Movement-history retention period in whole months, NULL meaning no retention
  period. It is the one policy column with no server default, because PROJECT_PROFILE §28 forbids a hard-coded
  retention number. It is stored for the archival maintenance and read by no production path.
- The sign-in and Scan Station role columns are listed under "Sign-in" and "Scan Station devices" below.
- Later policies are added as typed columns, with a server default where a canonical default exists. There is no
  key/value store.

**`worker_sessions`** (`0020_phase13_worker_sessions`) is the `ScanSession` of PROJECT_PROFILE:

- PK `id bigint` identity (never exposed by the API); `station_id` FK to `scan_stations (station_id)`; `area_id` FK to
  `areas (id)` (the station's Area at sign-in); `worker_id` FK to `workers (id)`; `started_at`, `expires_at timestamptz
  NOT NULL`; `ended_at timestamptz` and `end_reason text` nullable.
- CHECKs: `end_reason IN ('SWITCHED','EXPIRED','AREA_MODE_CHANGED','STATION_CHANGED','WORKER_DEACTIVATED')`;
  `(ended_at IS NULL) = (end_reason IS NULL)`; `expires_at > started_at`; `ended_at` NULL or within `[started_at,
  expires_at]`; `end_reason = 'EXPIRED'` only with `ended_at = expires_at`.
- `UNIQUE (id, worker_id, station_id)` is the target of the composite foreign key.
- Indexes: the partial UNIQUE `uq_worker_sessions_open_station` on `(station_id) WHERE ended_at IS NULL` (at most one
  open session per station) and `ix_worker_sessions_open_worker` on `(worker_id) WHERE ended_at IS NULL`.
- A mutation guard trigger forbids DELETE and TRUNCATE and lets an UPDATE change only an open row's `expires_at`,
  `ended_at` and `end_reason`.
- The foreign keys keep `NO ACTION`, because stations, Areas and Workers are never deleted.

### Users and roles

**`roles`** (`0028_phase13_users_roles`): editable named roles. PK `id` (identity); `name text NOT NULL` with the plain
`uq_roles_name` UNIQUE (case-sensitive, over the trimmed name); `created_at`, `updated_at`. There is no active flag and
no delete: a role is renamed, never removed. The migration seeds `Administrator`, `Manager` and `Operator` with exactly
the grants PROJECT_PROFILE §20 lists (17, 10 and 10 permission keys). They are ordinary rows, and nothing is keyed to
their names.

**`role_permissions`** (`0028_phase13_users_roles`): one row per grant. PK `pk_role_permissions` `(role_id,
permission)`; `role_id` FK `fk_role_permissions_role_id_roles` to `roles (id)`; `permission text NOT NULL` with
`ck_role_permissions_permission_known`, which admits exactly the 35-key vocabulary (one key per PROJECT_PROFILE §20
capability: `Permission` in `app/domain/enums.py`, repeated as a literal in the migration and pinned equal by a schema
test). A later capability widens the CHECK; a key is never renamed.

**`users`** (`0028_phase13_users_roles`): application accounts, never Workers.

- PK `id` (identity).
- `login_name text NOT NULL` with `uq_users_login_name` UNIQUE (across active and inactive Users) and
  `ck_users_login_name_canonical` (`login_name COLLATE "C" ~ '^[a-z0-9._@+-]{1,128}$'`), so the stored value is the
  trimmed, lowercase ASCII form the Application produces.
- `display_name text NOT NULL`.
- `role_id integer NOT NULL` FK `fk_users_role_id_roles` to `roles (id)` (exactly one role).
- The optional avatar `avatar_image`, `avatar_image_type`, `avatar_image_updated_at` under `ck_users_avatar_image_shape`,
  `ck_users_avatar_image_type` and `ck_users_avatar_image_size` (the `workers` avatar rules: all or none, PNG, JPEG or
  WebP, 1 byte to 2 MiB).
- `theme_preference text` nullable with `ck_users_theme_preference` (`DARK` or `LIGHT`; NULL means no preference),
  written only by the User's own toggle (`PUT /api/session/theme-preference`; not audited) and read with the session
  principal.
- `is_active boolean NOT NULL DEFAULT true`; `created_at`, `updated_at`.
- There is no credential column and no foreign key between `users` and `workers` in either direction.
- The downgrade of `0028_phase13_users_roles` refuses while any `users` row or any `User` or `Role` audit row exists.

### Sign-in

The sign-in migration `0029_phase14_sign_in` adds the following.

- `application_policy` columns: `user_session_expires boolean NOT NULL DEFAULT true`, `user_session_days integer NOT
  NULL DEFAULT 30`, `sign_in_lockout_attempts integer NOT NULL DEFAULT 10`, `sign_in_lockout_minutes integer NOT NULL
  DEFAULT 15` and `require_password_change boolean NOT NULL DEFAULT true`, with
  `ck_application_policy_user_session_days_range` (1–365), `ck_application_policy_sign_in_lockout_attempts_range`
  (3–100) and `ck_application_policy_sign_in_lockout_minutes_range` (1–1440).
- **`user_credentials`**: PK `pk_user_credentials` `user_id` (FK `fk_user_credentials_user_id_users` to `users (id)`:
  one credential per User); `password_hash text NOT NULL` (scrypt; `ck_user_credentials_password_hash_format`: begins
  with `scrypt$`); `password_is_temporary boolean NOT NULL`; `password_changed_at timestamptz NOT NULL`;
  `failed_attempts integer NOT NULL DEFAULT 0` (`ck_user_credentials_failed_attempts_non_negative`); `locked_until
  timestamptz` nullable; `created_at`. The credential is a separate table, never a `users` column, so a response that
  serializes a user can never carry it.
- **`user_sessions`**: PK `pk_user_sessions` `id bigint` identity; `user_id` FK `fk_user_sessions_user_id_users`;
  `token_digest bytea NOT NULL` (the SHA-256 digest of an opaque token; the token itself is never stored) with
  `uq_user_sessions_token_digest` UNIQUE and `ck_user_sessions_token_digest_length` (32 bytes); `created_at`;
  `ended_at` and `end_reason` nullable together (`ck_user_sessions_end_shape`), with `ck_user_sessions_end_reason`
  (`SIGNED_OUT`, `REPLACED`, `PASSWORD_CHANGED`, `PASSWORD_RESET`, `USER_DEACTIVATED`). Ended rows are kept.
  `ix_user_sessions_user_id_open` indexes a User's open sessions.
- `audit_events`, `machine_lifecycle_events` and `work_order_allocations` each gain the nullable `actor_user_id` with
  the foreign key named `fk_<table>_actor_user_id_users` (§16).
- The downgrade refuses while any credential, session, `actor_user_id` value or `sign-in` policy audit row exists, or
  while any sign-in policy column differs from its default.

### Scan Station devices

The migration `0030_phase14_station_devices` adds the following.

- **`scan_station_devices`**: PK `pk_scan_station_devices` `id` (identity); `station_id text NOT NULL` FK
  `fk_scan_station_devices_station_id_scan_stations` to `scan_stations (station_id)`; `label text NOT NULL` (trimmed,
  1–80 characters, an Application rule); `enrollment_code_digest bytea` nullable with
  `uq_scan_station_devices_enrollment_code_digest` UNIQUE and `ck_scan_station_devices_enrollment_code_digest_length`
  (`octet_length = 32`); `enrollment_expires_at timestamptz NOT NULL`; `token_digest bytea` nullable with
  `uq_scan_station_devices_token_digest` UNIQUE and `ck_scan_station_devices_token_digest_length`
  (`octet_length = 32`); `replaces_device_id integer` nullable FK
  `fk_scan_station_devices_replaces_device_id_scan_station_devices` to `scan_station_devices (id)`; `issued_at
  timestamptz NOT NULL DEFAULT now()`; `activated_at`, `last_seen_at`, `revoked_at` timestamptz nullable;
  `revoked_reason text` nullable.
- Only SHA-256 digests are stored. A pending row holds the code digest, and activation clears it while setting the token
  digest, so the two never coexist (`ck_scan_station_devices_code_or_token`) and a used code cannot match again.
- Further checks: `ck_scan_station_devices_activation_shape` (a token digest exactly when `activated_at` is set),
  `ck_scan_station_devices_revocation_shape` (`revoked_at` exactly when `revoked_reason` is set),
  `ck_scan_station_devices_revoked_reason` (`REVOKED`, `REPLACED`), `ck_scan_station_devices_replaced_shape`
  (`REPLACED` only for an activated device), `ck_scan_station_devices_last_seen_shape` and
  `ck_scan_station_devices_no_self_replace`.
- Rows are never deleted by the application, and there is no secondary index.
- `application_policy` gains `scan_station_role_id integer NOT NULL` with `fk_application_policy_scan_station_role_id_roles`
  to `roles (id)`: the role applied at Scan Stations. The migration sets it once to the seeded Operator role (the only
  place a role is resolved by name; the upgrade is refused while no role is named Operator), and no API writes it.
- `ck_audit_events_entity_type` is widened with `ScanStationDevice`.
- The downgrade refuses while any `scan_station_devices` row or `ScanStationDevice` audit row exists; otherwise it
  restores the previous entity-type CHECK and drops the pointer and the table.

### Beyond-demand correction

The migration `0031_phase14_beyond_demand` adds `work_order_allocations.exceeds_demand boolean NOT NULL DEFAULT false`
(every historical row keeps `false`) and `ck_work_order_allocations_exceeds_demand_shape` (`NOT exceeds_demand OR
(source = 'MANAGEMENT' AND allocation_reason IS NOT NULL AND reverses_allocation_id IS NULL AND station_id IS NULL AND
allocated_by_worker_id IS NULL AND actor_user_id IS NOT NULL)`). A correction is therefore a Management row with a
reason, never a reversal, never attributed to a Scan Station or a Worker, and always recorded with a signed-in User. The
downgrade refuses while any correction row exists; otherwise it drops the CHECK and the column.

### Migration boundary of the slice

The migrations that created this slice's schema (`0002_phase3_domain`, `0004_phase4_audit` and
`0005_phase4_release_index`) contain **no** foreign key to a table they do not create. `scan_stations` and `machines`
are environment-setup configuration created by `0003_phase35_environment` (§2, §18); the station, Machine, Worker and
correction columns of `part_movements` and `quantity_flows` were added by later migrations (§18).

### Cross-row invariants

PostgreSQL cannot express the following invariants declaratively:

- the projection agrees with the latest effective Movement (§15);
- the first Movement of a flow created by a release is its `RECEIVED`;
- a Movement's `assigned_route_step_id` agrees with its flow's route mode and belongs to that flow's own AssignedRoute
  (§11);
- every audited change commits with its audit row (§16);
- one canonical PN appears at most once among a Work Order's current demand lines (§5).

The transaction protocol enforces them (§13, §16, including the WorkOrder row lock that serializes the addition of
demand lines), and replay and reconciliation checks (§15) and concurrency tests verify them.

### Database roles and grants

- Privileges are not part of any migration. They are derived from one table classification in code
  (`backend/app/infrastructure/database_privileges.py`) and applied by `python -m app.cli apply-grants`, which every
  `migrate` also runs in its own transaction.
- The production stack has three database roles:
  - the owner (`POSTGRES_USER`, a superuser), which owns every object and is used only by `db`, `migrate` and
    `db-roles`;
  - `partflow_app`, used by the backend;
  - `partflow_maintenance`, provisioned and used by no service until the archival maintenance exists.
- Both application roles are `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, hold no membership
  and own nothing. Privileges are exact sets; no TRUNCATE, REFERENCES, TRIGGER or grant option is ever granted.

| Class | `partflow_app` privileges | Tables (29 at head `0032_phase14_route_adjusted`) |
|---|---|---|
| `APPEND_ONLY` | SELECT, INSERT | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| `GUARDED_UPDATE` | SELECT, INSERT, UPDATE | `worker_sessions` (the row trigger still refuses every change but open-session expiry and end) |
| `NO_UPDATE` | SELECT, INSERT, DELETE | `assigned_route_steps` (past steps are immutable by trigger and privilege; an adjustment deletes unreferenced future steps) |
| `READ_ONLY` | SELECT | `alembic_version` |
| `ORDINARY` | SELECT, INSERT, UPDATE, DELETE | `application_policy`, `areas`, `assigned_routes`, `departments`, `machine_asset_tag_config`, `machines`, `operations`, `part_numbers`, `quantity_flows`, `role_permissions`, `roles`, `route_steps`, `route_templates`, `scan_station_devices`, `scan_stations`, `user_credentials`, `user_sessions`, `users`, `work_order_demands`, `work_orders`, `workers` |

- `partflow_maintenance` holds SELECT only, on `alembic_version`, `application_policy`, `areas`, `machines`,
  `operations`, `part_movements`, `quantity_flow_lineage`, `quantity_flows`, `role_permissions`, `roles`,
  `scan_stations`, `users`, `worker_sessions` and `workers`.
- Neither role holds any sequence privilege (identity columns), or `CREATE` on schema `public` or on the database.
  PUBLIC holds nothing on any PartFlow table or sequence, and default privileges for PUBLIC and both roles are removed.
- The column guards on `areas` and `machines` stay trigger-only.
- Grants are never restored from a dump: every `migrate` re-derives them from the code head, and `apply-grants` does so
  after a restore.
- A new table must be classified in the same commit; a test fails otherwise, and `apply-grants` and `migrate` refuse an
  unclassified table.
- The reconciliation command's check (h) verifies the result (triggers, source hashes of the guard functions, grants,
  role attributes) and reports `not_applicable` on a database without the roles.
- A superuser that disables a trigger is outside detection; this is an accepted limitation (IMPLEMENTATION_ROADMAP
  Phase 16).

---

## 18. Explicitly Deferred Capabilities

The capabilities this slice left out fall into two groups: those that later work delivered, and those that are still
deferred.

### Delivered after this slice

Each of these capabilities is implemented. The table names the schema it added and where it is specified now; the
current columns and constraints are listed in §17, and the roadmap phase is given only to find its section in
`IMPLEMENTATION_ROADMAP.md`.

| Capability | Schema it added | Where it is specified now |
|---|---|---|
| Environment setup | `scan_stations` and `machines` tables, `areas.is_terminal` and the Area and Operation configuration fields (`0003_phase35_environment`) | PROJECT_PROFILE §8.4 to §8.6 and §15; IMPLEMENTATION_ROADMAP Phase 3.5 |
| Scan Station transfer, `TRANSFERRED` | widened movement-type check; `part_movements.station_id` (nullable FK to `scan_stations.station_id`, NULL on the Management-initiated `RECEIVED`); the per-type shape check `ck_part_movements_movement_shape` replacing the single-type `RECEIVED` check. The `TRANSFERRED` metadata carries the same request-fingerprint idempotency as §14, plus a `route_deviation` block for a confirmed Planned Route deviation (`0006_phase5_transfer`) | PROJECT_PROFILE §15 and §17; IMPLEMENTATION_ROADMAP Phase 5 |
| Machine assignment (one-shot, no sessions), `ASSIGNED_TO_MACHINE` and `RELEASED_FROM_MACHINE` | `quantity_flows.current_machine_id` (nullable FK to `machines.id`, indexed); the Movement Machine columns `source_machine_id` and `destination_machine_id`; widened movement-type check; the shape check pins each type's Area, Station and Machine references. `part_movements.command_sequence` (NOT NULL, default 1) numbers the Movements of one application command, and `UNIQUE (device_event_id, command_sequence)` replaces `UNIQUE (device_event_id)`; the release stays a one-Movement command and its §14 idempotency is unchanged (`0007_phase6_machine_assignment`) | PROJECT_PROFILE §12; IMPLEMENTATION_ROADMAP Phase 6 |
| Area completion, `AREA_COMPLETED`, and the derived `READY_TO_TRANSFER` holding state | `AREA_COMPLETED` clears `quantity_flows.current_machine_id` while `current_area_id` stays. A transfer from `ON_MACHINE` quantity appends `AREA_COMPLETED` (sequence 1) and `TRANSFERRED` (sequence 2) under ONE `device_event_id` in ONE transaction. The projection replay derives `QUEUED`, `ON_MACHINE` and `READY_TO_TRANSFER` from the latest Movement. `0008_phase7_direct_processing` widens the `AREA_COMPLETED` shape branch to allow a NULL source Machine (a completion never carries a destination Machine) | PROJECT_PROFILE §7 Area Completion and §12; IMPLEMENTATION_ROADMAP Phases 6 and 7 |
| Direct Area processing (Areas without Machines) | only the `AREA_COMPLETED` shape widening above, no column. The derived holding state `PROCESSING` (an arrival Movement in an Area without active Machines) is never stored, and no `machine_assignment_mode` exists: behaviour follows from the Area's Machines (`0008_phase7_direct_processing`) | PROJECT_PROFILE §12; IMPLEMENTATION_ROADMAP Phase 7 |
| SPLIT and MERGED, partial movement | `SPLIT` and `MERGED` types with one shared shape branch; the `quantity_flows.status` and `closed_at` lifecycle checks; the append-only `quantity_flow_lineage` edge table (parent to child, relation, `device_event_id`) instead of a `parent_flow_id` column, with one edge per SPLIT child (1 to N) and one per MERGED source (N to 1) (`0009_phase8_split_merge`) | PROJECT_PROFILE §11; IMPLEMENTATION_ROADMAP Phase 8 |
| Undo and corrections, `REVERSED`, `reverses_movement_id`; Repair (`movement_reason = REPAIR`), `SCRAPPED`, `QUANTITY_ADJUSTED` additions | `movement_reason` (only `REPAIR`, only on a `TRANSFERRED`); the free-text `reason` (mandatory for Scrap, adjustments and Repair, enforced by a CHECK); `reverses_movement_id` (FK to `part_movements`, set exactly on a `REVERSED`, UNIQUE, so at most one reversal per original); the movement-type and shape checks widened with `SCRAPPED`, `QUANTITY_ADJUSTED` and `REVERSED`; `quantity_flows.status` widened with `SCRAPPED` and `REVERSED` (`0010_phase9_undo_corrections`). Undo appends compensating `REVERSED` rows for the COMPLETE command, and every derivation excludes the reversed pair, so the restored state is the Movement before the undone command. `QUANTITY_ADJUSTED` introduces a NEW flow (like a `RECEIVED`, recorded at a station) and never edits an existing quantity. The Undo reason policy (`application_policy.undo_reason_required`, `0022_phase13_undo_reason_policy`) requires `reason` on the `REVERSED` rows while it is on; the Undo command enforces it, never a CHECK, because it depends on configuration | PROJECT_PROFILE §14 and §16; IMPLEMENTATION_ROADMAP Phase 9 |
| Stockroom, `STOCKED`, WorkOrderAllocation | the movement-type and shape checks widened with `STOCKED` (the same shape as a `TRANSFERRED`: two different Areas at a Station, no Machine; the terminal destination is an Application rule judged under the Area lock); `quantity_flows.status` widened with `STOCKED` (the flow closes as manufacturing-complete); `work_orders.completed_at` as a projection of the allocation rows; the append-only `work_order_allocations` table of allocation and reversal rows (canonical PN by value, the demand FK, `reverses_allocation_id` UNIQUE, `device_event_id` and `command_sequence`), which never references a Movement or a QuantityFlow, because allocation stays separate from Movement by design; `work_order_demands.allocated_quantity` as a maintained projection of the demand's active allocation rows (`0011_phase10_stock_allocation`). `exceeds_demand` was added by `0031_phase14_beyond_demand` | PROJECT_PROFILE §8.12 and §18; IMPLEMENTATION_ROADMAP Phase 10 |
| Monitoring read models | Movement-derived queries; the Tracking index `ix_part_movements_part_number_occurred_at_id` (`0012_phase11_tracking_index`) | PROJECT_PROFILE §21; IMPLEMENTATION_ROADMAP Phase 11 |
| Priority and Hot list management | no new column: the existing `priority_rank` gained the positive CHECK and the UNIQUE (dense `1..N`, §5 and §17) after a refusing pre-check, plus the audit expression index (§17). Its writers are the Hot list command and the automatic or line-deletion removal (`hot_ranks`), and both audit through `audit_events` (§16) (`0013_phase12_priority`) | PROJECT_PROFILE §7 Priority and §21 Priority Management; IMPLEMENTATION_ROADMAP Phase 12 |
| Administration | the master-data tables already existed; the Department display settings and the Due Soon policy (`0025_phase13_display_settings`); the Movement-history retention period setting (`0027_phase13_retention_period`). Executing retention is still deferred (below) | PROJECT_PROFILE §21 Administration; IMPLEMENTATION_ROADMAP Phases 3.5 and 13 |
| PartNumber metadata management, image and hard delete | nullable columns on `part_numbers`; no production table touched (`0023_phase13_part_number_master`) | PROJECT_PROFILE §8.1; §6 and §16 here |
| Scan Station theme preference (station tier) | one nullable `scan_stations.theme_preference` column with a CHECK (`DARK` or `LIGHT`); not audited (`0026_phase13_station_theme`) | GUI_DESIGN; §16 here |
| User theme preference (User tier) | one nullable `users.theme_preference` column with a CHECK (`DARK` or `LIGHT`); not audited (no migration of its own; the column comes from `0028_phase13_users_roles`) | GUI_DESIGN; §16 and §17 here |
| Planned Routes management | two nullable `preferred_machine_id` columns (FK on `route_steps`, none on `assigned_route_steps`), one index and the `RouteTemplate` audit vocabulary; no backfill (`0024_phase13_planned_routes`) | PROJECT_PROFILE §8.8, §8.9 and §17; §10, §16 and §17 here |
| Authentication and roles | users, roles and permissions as configuration (`0028_phase13_users_roles`); sign-in, sessions and the actor column (`0029_phase14_sign_in`); permission enforcement on Administration and on Management reads and writes (no migration); Scan Station device enrollment and station permissions (`0030_phase14_station_devices`). There is no schema coupling to Movement; `actor_user_id` sits beside the legacy `actor_reference` text column, which is never backfilled (§16) | PROJECT_PROFILE §20; IMPLEMENTATION_ROADMAP Phase 14 |
| AssignedRoute adjustment (`ROUTE_ADJUSTED`) | the audit vocabulary widening, the UNIQUE partial idempotency index, the `part_movements.assigned_route_step_id` index and the UPDATE-forbid trigger on `assigned_route_steps`; no Movement type (`0032_phase14_route_adjusted`) | PROJECT_PROFILE §8.10; §14, §16 and §17 here |
| PN audit trail reader | none: it reads `audit_events` and `work_order_allocations` only | §14 here; IMPLEMENTATION_ROADMAP Phase 14 |
| File-based Work Order import | none: new Work Orders, and changes to Open and Released Work Orders through the ordinary demand edit, reusing the §5 validation idempotently | PROJECT_PROFILE §13; §5, §14 and §16 here; IMPLEMENTATION_ROADMAP Phase 15 |
| Worker identification and ScanSession persistence | the `workers` table (badge UNIQUE over the canonical form, avatar on the row) and the audit vocabulary widening (`0014_phase13_workers`); `part_movements.worker_id`, `work_order_allocations.allocated_by_worker_id`, `areas.worker_identification_mode` and `areas.fixed_worker_id` (`0019_phase13_worker_identity`); `part_movements.scan_session_id`, `worker_sessions`, `application_policy` and `areas.worker_session_timeout_minutes` (`0020_phase13_worker_sessions`); the three badge-confirmation columns of `application_policy` (`0021_phase13_badge_confirmation`) | PROJECT_PROFILE §8.13 and §19; §11, §16 and §17 here |

### Still deferred

| Capability | State | Path |
|---|---|---|
| ERP fields and synchronization | deferred and not approved (IMPLEMENTATION_ROADMAP Deferred) | an isolated integration boundary; `device_event_id` stays compatible |
| Offline scan synchronization | an open decision (PROJECT_PROFILE §32 item 2); production writes stay blocked while disconnected | `device_event_id` was chosen so that a future approved offline design needs no renaming (§20) |
| Return of stocked quantity to active production | an open decision (PROJECT_PROFILE §32 item 1) | it may later widen the movement-type check additively (§20) |
| Administrative Movement-history archival and purge | planned in IMPLEMENTATION_ROADMAP Phase 16; the retention period is stored (`application_policy.retention_period_months`) but read by no production path | a separate privileged maintenance path under the `partflow_maintenance` role (PROJECT_PROFILE §28; §11 and §17 here) |
| Further file-import capabilities | deferred until an owner decision asks for them (IMPLEMENTATION_ROADMAP Deferred) | no schema change is planned |

---

## 19. Acceptance Criteria

1. Saving a Work Order with WorkOrderDemand creates no QuantityFlow, no PartMovement and no projection change.
2. Creating a new PN (on first valid use) normalizes the entered value to the canonical PN: `abc-123`, `AbC-123`,
   `ABC-123` and `" ABC-123 "` all resolve to the single canonical PN `ABC-123` (surrounding whitespace trimmed;
   constraint-verified, so a second master row for the same canonical PN is impossible), and any value with internal
   whitespace after trimming (`"ABC 123"`, `"ABC\t123"`, `"ABC\n123"`) is rejected with no write. The barcode is derived
   as `PF:PN:<part-number>` from the canonical PN. The master creation appends a `CREATED` audit event.
3. A release creates exactly one QuantityFlow (with its route mode), one `RECEIVED` Movement, and, for a `PLANNED`
   release only, one AssignedRoute snapshot, atomically and with no generic audit event. A `FLOATING` release (the
   default) creates no AssignedRoute. If any part fails, nothing commits: no committed state can contain a QuantityFlow
   without its `RECEIVED` Movement, a `PLANNED` flow without its snapshot, or a `FLOATING` flow with one
   (constraint-verified through the flow's `assigned_route_id` CHECK, §17), and no partial release state is ever
   observable.
4. `current_area_id` is set to the confirmed starting Area by the QuantityFlow INSERT itself; the column is NOT NULL,
   and no projection update after the insert is required for release.
5. Every `RECEIVED` Movement records a resolved Operation (`operation_id NOT NULL`, constraint-verified). For a
   `PLANNED` flow it references the AssignedRoute snapshot's first step (`assigned_route_step_id` set to an
   `assigned_route_steps` row, never a `route_steps` row); for a `FLOATING` flow `assigned_route_step_id` is NULL.
6. Releasing with an invalid PN, an inactive Area, an inactive Operation, an inactive RouteTemplate (when `PLANNED`), or
   a quantity ≤ 0 is rejected with no write.
7. Releasing a PN that already has active quantity without the explicit confirmation flag is rejected with no write;
   with confirmation it creates a separate flow and never merges.
8. Retrying a release with the same `device_event_id` and the same normalized request (matching fingerprint) returns
   the original result and creates nothing.
9. Reusing a `device_event_id` with a different normalized request (mismatched fingerprint) returns an explicit
   idempotency-conflict error and creates nothing.
10. Editing a RouteTemplate after release does not change any AssignedRoute snapshot, and template edits never alter
    the route context recorded by any existing Movement.
11. The projection rebuild procedure reproduces `current_area_id` for every flow from Movement history alone.
12. `part_movements` and `audit_events` rows cannot be updated or deleted by the application role. The trigger is
    verified by the schema tests; the permission is verified by the database-role tests
    (`tests/test_database_roles.py`) and by running the test suite under the application role.
13. Every WorkOrder and WorkOrderDemand creation or edit appends an `audit_events` row (`CREATED` or `UPDATED`, with
    `before_data` and `after_data`) in the same transaction as the change; edits preserve all prior audit rows unchanged
    (append-only history, verified by test).
14. The writes of this slice append `audit_events` rows only for `WorkOrder`, `WorkOrderDemand` and `PartNumber`, and
    `audit_events` never contains a row for production release or any other production activity (constraint- and
    test-verified; the entity-type CHECK of §17 admits no production entity).
15. Conservation holds: Σ(active flow quantities per PN) = Σ(`RECEIVED` quantities per PN).
16. Releasing into a **terminal** Area is rejected with no write, whatever the UI offered.
17. A demand releases in parts until its remaining quantity is exhausted: each part creates its own QuantityFlow and
    `RECEIVED` (never a merge), the released and remaining quantities derive from Movement history alone, a release
    beyond the remainder, or any release once it is 0, is rejected with no write, and the Work Order reads `RELEASED`
    only once every line's remainder is 0.
18. A demand line that has released quantity takes the restricted edit: its `requested_quantity` (never below the
    released or allocated quantity), `due_date` and `job_numbers` save with a normal `UPDATED` audit row; raising the
    quantity restores remaining quantity and the Work Order's derived `OPEN`; and an edit of `request_type`,
    `requester`, `reason` or `notes`, or a quantity below what is committed, is rejected with no write at all. Its prior
    audit history, its released quantity and the Work Order header edit are unaffected.
19. The migrations that created this slice's schema (`0002_phase3_domain`, `0004_phase4_audit`,
    `0005_phase4_release_index`) contain no foreign key to a table they do not create, and no deferred column: no
    `machines`, `workers`, `scan_sessions`, `scan_stations` or user-table references, and no `current_machine_id`,
    `parent_flow_id`, `station_id`, `worker_id`, `scan_session_id`, `reverses_movement_id`, `movement_reason`,
    `is_terminal`, `worker_identification_mode`, `preferred_machine_id` or `completed_at`. There is no
    `machine_assignment_mode` at all: Area behaviour follows from its Machines (PROJECT_PROFILE §12).

---

## 20. Remaining Uncertainty

Only the open decisions of PROJECT_PROFILE §32 touch this slice, and neither blocks it:

- **Return from stock to active production** (PROJECT_PROFILE §32 item 1) may later widen the movement-type check;
  the change would be additive. Until it is decided, a `STOCKED` command is never undone; allocation adjustments are
  the correction path.
- **Offline scan synchronization** (PROJECT_PROFILE §32 item 2): this slice assumes synchronous online semantics (§14).
  `device_event_id` was chosen so that a future approved offline design would not require renaming.

---

## Change history

| Date | Commit | Change |
|---|---|---|
| 2026-11-09 | uncommitted | Rewritten as current content for new readers; the schema description now matches migration head `0032_phase14_route_adjusted` and the release code; no rule changed. |
| 2026-10-08 | `1116b07` | Production database roles and grants, with the append-only revocation applied by `apply-grants`. |
| 2026-10-07 to 2026-10-08 | `e1dee40`, `918f025`, `e13574f` | File-based Work Order import: intake marker, idempotency by Work Order Number, received-date default on the site calendar. |
| 2026-10-07 | `961c0a1`, `1c9d755`, `582baf0` | Read-only PN audit trail reader; User theme tier. |
| 2026-10-07 | `4a77dc4`, `c939141` | Assigned Route adjustment (`ROUTE_ADJUSTED`), immutable past snapshot steps and the adjustment's idempotency index. |
| 2026-10-07 | `98ba54c` | Management allocation and the beyond-demand correction (`exceeds_demand`). |
| 2026-10-06 to 2026-10-07 | `702ab1c`, `f0ae395`, `77fd32c`, `1c5b3ef` | Sign-in, permission enforcement, the `actor_user_id` columns and Scan Station devices. |
| 2026-10-04 to 2026-10-06 | `c12728d` to `69b8ec1` | Full Administration: Workers, configuration audit, Worker identification and sessions, policies, Part Numbers management, Planned Routes, display settings, retention period, Users and Roles. |
| 2026-10-04 | `80f7925`, `9f466fa` | Hot list priority: dense unique ranks, automatic removal of inactive entries and confirmed deletion of a Hot line. |
| 2026-09-02 to 2026-09-04 | `d2f68de`, `8e5ab18`, `f10d8bd` | Vietnamese mirror added; a Scan Station receipt never joins existing quantity. |
| 2026-08-26 to 2026-09-01 | `b031ad6` to `100d59d` | Direct Area processing, SPLIT and MERGED, Undo and corrections, Stockroom and allocation recorded as delivered. |
| 2026-08-20 to 2026-08-25 | `fc83be4` to `a567b02` | Partial release of one demand, restricted edit after release, the release-evidence index and Scan Station transfer. |
| 2026-08-17 | `2a57c50`, `6a16fe1` | Synchronized with PROJECT_PROFILE v19; the route-step agreement stated as a cross-table invariant. |
| 2026-08-12 | `fa4b51d`, `c4f18a6` | Canonical PN string as identity, optional hard-deletable PN master, trim-then-reject normalization. |
| 2026-07-28 to 2026-08-04 | `fbb38cb` to `74e0386` | Floating routes, nullable Work Order Numbers, Area completion state and Planned Routes management. |
| 2026-07-22 to 2026-07-26 | `0e659ac` to `74edbdd` | First data model of the slice: single-record audit, strict `RECEIVED`, idempotency fingerprint, nullable due dates. |
