# PartFlow Project Profile v11

> **Status:** Living Document
> **Authority:** Canonical project profile for PartFlow domain behavior and product direction

---

# 1. Project Overview

## Purpose

PartFlow is an internal shop-floor tracking system designed to track Part Numbers and physical production quantities as they move through manufacturing Areas.

The system must always be able to answer:

- Which Part Number is being processed.
- Which Areas currently hold quantities of that Part Number.
- How many physical parts are currently in each Area.
- Which Operation is being performed.
- Which Machine is currently processing a quantity, when applicable.
- Which Work Orders request that Part Number.
- How many pieces each Work Order requires.
- What production work remains.
- Which route each active quantity is expected to follow.
- Which Areas and Machines each quantity has passed through.
- Whether requested quantities have been completed and stocked.
- How completed quantities are allocated to Work Orders.

PartFlow is intentionally focused on lightweight production tracking.

It is not an ERP, MES, inventory management system, production scheduler, accounting system, or machine-control platform.

ERP integration may be added later, but the application must remain independently usable without ERP connectivity.

The initial deployment targets the Machine Shop Department. The architecture must remain flexible enough to support additional Departments without changing the core tracking model.

---

# 2. Design Goals

PartFlow is designed around real shop-floor operations rather than idealized manufacturing workflows.

The system must prioritize:

1. Tracking accuracy.
2. Operational simplicity.
3. Fast barcode-driven workflows.
4. Complete and immutable movement history.
5. Minimal operator interaction.
6. Reliable quantity integrity.
7. Clear production visibility.
8. Long-term maintainability.

Whenever trade-offs exist, practical shop-floor usability takes precedence over theoretical perfection, provided tracking accuracy and data integrity are preserved.

The application should allow operators to record real production activity with as few interactions as possible.

---

# 3. Design Principles

## Scanner First

Barcode scanning is the primary production interaction.

- Keyboard-wedge scanners must work without custom drivers.
- The normal workflow must not require mouse interaction.
- Touch interfaces should be optimized for tablets and fixed workstations.
- Scan input must automatically regain focus after every completed operation.
- Manual entry must remain available as a fallback.

---

## Part Number-Centric

The Part Number, abbreviated as PN, is the primary tracked identity.

The system does not use `Work Order + PN`, a physical batch, or an individual piece as the primary tracked object.

Instead:

- PN identifies the reusable part definition.
- Physical quantity represents production state.
- Work Order Demand represents business demand.
- Part Movement represents production activity.
- Work Order Allocation connects stocked quantity to individual Work Order Demand only after completion.

---

## Quantity-Based Tracking

PartFlow tracks physical quantities, not individually identified pieces.

A PN may simultaneously have quantities:

- in multiple Areas,
- assigned to multiple Machines,
- queued and actively processing,
- following different quantity routes.

The system must never assume that one PN has only one current location or one active processing path.

---

## Production-Oriented

The application tracks how production actually moves through the shop.

Operators must not be forced to perform administrative steps that do not correspond to real physical work.

Production data should represent reality rather than an idealized workflow.

---

## Production Reflects Reality

Routes, plans, and expected durations provide guidance.

Actual Movement history remains the source of truth.

If production deviates from the assigned route:

- the system should warn when appropriate,
- ambiguity must require confirmation,
- authorized deviations must be recorded,
- actual production must not be rejected merely because it differs from the plan.

Reality wins over the original plan.

---

## History Is Immutable

Every meaningful production movement must be recorded.

Corrections must create compensating, reversal, or adjustment events.

Normal workflows must never silently modify or delete historical Movement records.

---

## ERP Independent

PartFlow must function without ERP connectivity.

ERP data may be:

- entered manually,
- imported from files,
- synchronized later through an isolated integration boundary.

PartFlow business rules must never depend on ERP-specific response formats or APIs.

---

## Practical Before Perfect

Real manufacturing contains exceptions.

The application should help operators handle valid exceptions safely rather than rejecting every deviation.

When the system cannot determine intent with sufficient confidence, it must ask for explicit confirmation instead of guessing.

Unknown or ambiguous input must never update tracking data.

---

# 4. Operational Context

Each reusable manufactured design is identified by a unique Part Number.

A reusable physical folder containing drawings is maintained for every PN.

The folder:

- belongs to the PN, not to a specific Work Order,
- is reused across all Work Orders requesting that PN,
- may be reused across production runs,
- carries one unique PN barcode,
- may contain drawings for the current revision.

The company intentionally does not label every physical part because:

- parts may begin as raw material,
- labels may not survive machining,
- attaching labels to every piece is operationally impractical,
- staffing does not support individual piece tracking.

PartFlow therefore tracks physical quantities associated with a PN.

---

## Shared Drawing Folder

The drawing folder is primarily needed during setup and reference work.

After setup is complete:

- production quantities may continue moving independently,
- the same folder may temporarily be used by another Area,
- the folder may be physically separated from some or all active quantities.

The physical location of the folder is not the production location of the PN quantity.

The system must never assume that:

- the folder and all physical parts are together,
- scanning the folder means all active quantity moved,
- only one Area may work on the PN at a time.

The barcode identifies the PN. Quantity and movement context are resolved by the application.

---

# 5. Core Tracking Rules

The primary tracked object is the PN.

A PN:

- is unique in the ERP system,
- has no guaranteed format,
- must be treated as an arbitrary string,
- has one reusable drawing folder,
- has one unique PartFlow barcode,
- may be requested by multiple active Work Orders,
- may have multiple external Job Numbers (used only for display, searching, sorting, and reporting),
- may have quantities in multiple Areas simultaneously,
- may have quantities assigned to multiple Machines simultaneously,
- may have different quantity flows following different Routes,
- must remain permanently traceable after first recording.

The system does not track:

- individual serial pieces,
- a physical batch as a first-class identity,
- `Work Order + PN` as the movement identity.

Instead:

- Movement is recorded at PN + quantity level.
- Work Order Demand is maintained separately.
- Stocked quantity is allocated to Work Order Demand after completion.
- Allocation changes must not rewrite shop-floor Movement history.

---

# 6. Fundamental Invariants

The following rules are mandatory:

1. A recorded PN must never become untraceable.
2. Movement history is immutable.
3. Current production state must be derived from Movement history whenever practical.
4. A PN may simultaneously have quantity in multiple Areas.
5. A PN may simultaneously have quantity assigned to multiple Machines.
6. Quantity must never be accidentally created, destroyed, duplicated, lost, or made negative.
7. No Movement may consume more quantity than is available in its source position.
8. Unknown or invalid scans are rejected; ambiguous scans must never update production data until the ambiguity is explicitly confirmed.
9. Work Order Demand and shop-floor Movement are separate concepts.
10. Work Order Allocation and shop-floor Movement are separate concepts.
11. Work Order Allocation may change without rewriting Movement history.
12. Routes guide production but do not replace actual Movement history.
13. Corrections must preserve the original event.
14. Scan updates must be atomic.
15. The MVP must work without ERP connectivity.

---

# 7. Canonical Vocabulary

The following names must be used consistently in documentation, source code, APIs, database objects, and user interfaces.

## Part Number

A reusable ERP-defined identifier for a manufactured part design.

Abbreviation: `PN`.

The PN is the primary tracked identity.

A PN:

- is unique,
- has no fixed format,
- remains the same across multiple Work Orders,
- is represented by the reusable folder barcode.

---

## Work Order

An externally originated manufacturing work container containing one or more requested Part Numbers and physical quantities that PartFlow is responsible for tracking.

Abbreviation: `WO`. The abbreviation is appropriate only in compact user-facing labels, table headings, and prose where Work Order has already been established; code names remain the full `WorkOrder`, `WorkOrderDemand`, and `WorkOrderAllocation`.

A Work Order Number:

- originates externally or is manually entered,
- has no fixed format,
- must be treated as an opaque, arbitrary string,
- is the canonical business container identifier used by PartFlow,
- is never reformatted, padded, or normalized once entered.

The external Work Order Number is **nullable**. The user may create demand without knowing an external Work Order Number: when the Work Order Number is left blank and the user explicitly confirms saving, PartFlow stores `work_order_number = NULL` on an internal Work Order. A Work Order without an external number:

- displays as `—` wherever a Work Order Number is presented (the `—` placeholder is display-only and is **never persisted**),
- is clearly labeled as an internal Work Order without an external number,
- may later receive the real external Work Order Number through an audited edit,
- keeps its own stable internal identity (a database key that is never the user-facing identifier).

No temporary Work Order Number (such as `TMP-YYYYMMDD-HHMMSS`) is ever generated — that earlier convention is removed. Multiple Work Orders may simultaneously have a null external number; uniqueness applies to non-null Work Order Numbers only. No UUID or other machine identifier is ever the user-facing Work Order identifier. Work Order Numbers that were entered by the user remain opaque arbitrary strings and are never reformatted.

Work Order Number and external Job Number are separate identifiers (for example Work Order Number `007125` versus external Job Number `17555`). Job Numbers remain informational metadata on Work Order Demand: usable for display, search, sorting, and reporting, but never an internal identity, a movement identity, or a workflow key.

---

## Work Order Demand

The requested physical quantity of one PN for one Work Order.

Work Order Demand contains the business context needed to answer:

- which Work Order requests the PN,
- requested quantity,
- allocated quantity,
- remaining shortage,
- due date,
- priority,
- request type,
- external Job Numbers (used only for display, searching, sorting, and reporting).

Work Order Demand does not define the current production location.

---

## Request Type

Describes why a Work Order Demand or internal demand exists.

Initial values:

- `NEW`
- `MODIFY`

`NEW` is externally requested production. `MODIFY` means physical quantity of a PN is introduced for modification work — an internal PartFlow concept that does not need to exist in ERP. A MODIFY intake may have no external Work Order Number, no predefined Route, and no pre-existing active Work Order Demand; PartFlow still creates the minimum internal records needed for quantity integrity and traceability (an internal WorkOrder with a null number, one WorkOrderDemand, a QuantityFlow, and immutable Movement history).

**Repair is not a Request Type.** `REPAIR` is an explicit production movement intent (a `movement_reason` on a transfer, §8.11): some or all quantity already in production returns to a previously visited Area to correct work performed earlier. Repair creates no new physical quantity and no new business demand.

---

## Department

A major organizational unit.

Examples:

- Machine Shop
- Purchasing
- Assembly
- Production
- Stockroom
- Quality Control

The initial release targets Machine Shop.

---

## Area

A physical production place where quantity is received, queued, processed, transferred, outsourced, or stocked.

Examples:

- Material
- Cut
- Lathe
- Mill
- Manual
- Deburr
- External
- Stockroom

An Area has a stable identity.

Display properties such as name, description, color, and icon may change without altering historical identity.

---

## Operation

The work performed in an Area.

Examples:

- Cutting
- Turning
- Milling
- Deburring
- Plating
- Painting
- Testing
- Receiving

An Area may support one or multiple Operations.

Example:

- Area: `External`
- Operations: `Plating`, `Painting`, `Testing`

Operation describes the work. Area describes the physical place.

---

## Machine

A physical production resource inside an Area.

Machine is optional.

A Machine:

- belongs to exactly one Area,
- has its own barcode,
- becomes the current executor when quantity is assigned to it,
- also identifies the physical processing location while assigned.

A Machine has a lifecycle: it is active until it is **retired**. A Machine that was ever referenced by Movement history is never hard-deleted — it is retired instead, stays visible for historical display and reporting, never appears in assignment choices, and accepts no new scans.

A Machine keeps a stable internal identity; the operator-facing display name is separate and may be reused. Replacing a physical Machine means retiring the old record and creating a **new** record with its own stable identity and its own new barcode. The new Machine may reuse the familiar floor-position display name (for example `Lathe 1`); the old record is never renamed or mutated, and the two remain distinguishable through internal identity and asset metadata. There is no MachineSlot or WorkCenter abstraction — the display name alone carries the floor-position familiarity.

---

## Quantity Flow (Internal Tracking Concept)

A traceable production portion of a PN quantity as it moves, splits, merges, queues, and becomes assigned to Areas or Machines.

Quantity Flow does not represent individually labeled pieces.

It is the logical identity needed to preserve:

- current quantity distribution,
- route assignment,
- split and merge history,
- Area and Machine progression.

Different Quantity Flows of the same PN may follow different Routes.

---

## Route and Route Mode

The ordered production path expected for a Quantity Flow.

Every Quantity Flow has a **route mode**:

- `FLOATING` (the **default**) — no predefined sequence is required. The actual route is derived from immutable Movement history: each visited Area extends the observed route trace, repeated Areas are preserved, different Quantity Flows of the same PN may have different traces, and split flows continue independently. A Floating Route has no AssignedRoute snapshot.
- `PLANNED` — the flow carries an AssignedRoute snapshot copied from a Route Template as guidance.

A Planned Route may contain:

- Areas,
- Operations,
- expected durations,
- instructions,
- preferred Machines.

A Route is guidance. Actual Movement history remains authoritative in both modes, and no second mutable route history may duplicate PartMovement.

---

## Part Movement

An immutable event recording the movement, assignment, completion, correction, split, or merge of PN quantity.

---

## Area Completion (DONE)

The explicit completion of processing at the current Area for a **selected physical quantity**:

- user-facing action/status: `DONE`;
- canonical immutable Movement type: `AREA_COMPLETED`;
- internal derived holding state: `READY_TO_TRANSFER`.

`DONE` means: the selected physical quantity has completed processing at the current Area and is ready to be transferred to another Area — it waits on the Area's finished rack. The current Machine clears from the resulting position; the current Area remains the physical location until the quantity is transferred.

`DONE` never means: PN completion, completion of every quantity of the PN, Work Order completion, manufacturing completion, stocked quantity, QC approval, or allocation to Work Order Demand. Manufacturing completion remains represented only by `STOCKED` at the terminal Stockroom.

DONE is quantity-scoped, never a global PartNumber status: it applies to a selected physical quantity of a specific current Quantity Flow/position. The same PN may simultaneously have quantities queued, on Machines, directly processing, finished and ready to transfer, in other Areas, stocked, and scrapped. There is no `PartNumber.status = DONE`, and the state remains reconstructable from immutable Movement history.

---

## Current Position

The derived current Area and optional Machine holding a Quantity Flow.

---

## Work Order Allocation

The assignment of stocked PN quantity to specific Work Order Demand records.

Work Order Allocation is independent from Part Movement.

---

## Priority

A manager-defined rank stored on Work Order Demand.

Priority is the highest allocation and work-ordering criterion.

---

## Hot Part

A UI and business label indicating expedited Work Order Demand.

`Hot Part` must not replace the underlying `priority_rank`.

---

## Worker

The operator performing or confirming production activity.

Worker identity exists for accountability and reporting.

Production correctness must not depend on Worker identity.

---

# 8. Domain Model

## 8.1 PartNumber

Represents the reusable PN master record.

Typical attributes (illustrative only):

- `id`
- `part_number`
- `barcode_value`
- `name`
- `description`
- `image_url`
- `current_revision`
- `erp_id`
- `is_active`
- `created_at`
- `updated_at`

Rules:

- `part_number` must be unique **case-insensitively**: `abc`, `ABC`, and `Abc` resolve to the same PartNumber. Persistence uses a normalized lookup key or case-insensitive unique constraint (e.g. a unique index on `lower(part_number)`); the stored and displayed value keeps the exact casing of first creation and is never silently re-cased.
- `part_number` must be treated as an arbitrary string.
- A PartNumber is **created on first valid use**: no pre-populated authoritative PN catalog is required, ERP is never called during MVP, and manual PN entry accepts any non-empty PN value. The internal PartNumber record is still required for references, tracking, metadata, history, and future ERP mapping.
- Real PN values are commonly multi-segment hyphenated numeric strings of varying length (shapes such as `214-406`, `78-04-0031`, `0455-20-0118-03`, `2027-60-8114-00`). The exact string must be preserved everywhere; PN segments must never be parsed for business meaning.
- `name`/`description` are free text as supplied — commonly uppercase with commas, slashes, fractions, dimensions, and manufacturing abbreviations (e.g. `VALVE, SOLENOID VITON, 3/8`) — and may be long enough to span multiple display lines.
- The folder barcode identifies only the PN and carries the PN itself: `PF:PN:<part-number>` (§10).
- An Administrator may archive (soft-delete) junk or test PartNumbers (§28 Administrative Archival and Purge): archived PNs disappear from active lookup and intake, while historical displays keep the original PN text with an explicit `(archived)` marker.
- Current revision is informational only.
- Revision changes do not create a new tracked PN unless ERP provides a different PN.
- The PN barcode is reused across all Work Orders requesting the PN.

---

## 8.2 WorkOrder

Represents a Work Order received by the business.

Typical attributes (illustrative only):

- `id`
- `work_order_number`
- `received_date`
- `due_date`
- `status`
- `erp_id`
- `created_at`
- `updated_at`

Rules:

- `work_order_number` must be treated as an arbitrary string.
- `work_order_number` is **nullable** (§7 Work Order). A null number is valid data on an internal Work Order, displays as `—` (never persisted as a placeholder), and may be replaced by the real external number through an audited edit. The database allows multiple Work Orders with a null number while preserving uniqueness for non-null numbers (partial unique index).
- `received_date` is required and defaults to the current date during manual creation.
- `due_date` may be null. A missing Work Order due date is valid data, not a validation error; it may be added later. The Work Order due date serves as the entry default for demand-line due dates.
- A Work Order contains one or more Work Order Demand records.
- A Work Order is complete when every Work Order Demand has been fully allocated.
- Completed Work Orders move out of active views but remain permanently available in history.

---

## 8.3 WorkOrderDemand

Represents how many physical pieces of a PN are requested by one Work Order.

Typical attributes (illustrative only):

- `id`
- `work_order_id`
- `part_number_id`
- `request_type`
- `requested_quantity`
- `allocated_quantity`
- `due_date`
- `priority_rank`
- `job_numbers`
- `requester`
- `reason`
- `notes`
- `created_at`
- `updated_at`

Rules:

- A PN may have multiple active Work Order Demand records.
- Requested quantity represents physical pieces.
- `due_date` may be null. A missing due date is valid data, not a validation error, and never blocks saving. Undated demand is ordered after all dated demand (§18 Allocation Order).
- The PN itself owns no due date. A PN presented without a due date means the relevant Work Order Demand has no due date.
- Work Order Demand may be edited by Admin or Manager.
- Priority belongs to Work Order Demand.
- Work Order Demand does not own shop-floor Movement.
- Work Order Demand does not determine current PN location.
- Allocation may be adjusted at any time by Admin or Manager.

---

## 8.4 Area

Represents a physical shop-floor location.

Typical attributes (illustrative only):

- `id`
- `department_id`
- `barcode_value`
- `name`
- `description`
- `color`
- `icon_url`
- `is_terminal`
- `is_active`
- `worker_identification_mode`
- `created_at`
- `updated_at`

Rules:

- Area identity and barcode must remain stable.
- Area display name may change.
- An Area may contain zero, one, or multiple Machines.
- An Area may support one or multiple Operations.
- Stockroom is normally a terminal Area.
- Assignment behavior follows from the Area's Machines (§12): an Area without Machines processes directly; an Area with Machines — one or many — always uses `QUEUE_AND_ASSIGN`. There is no per-Area machine-assignment configuration and no auto-assignment for single-Machine Areas.

---

## 8.5 Operation

Represents a type of work supported by an Area.

Typical attributes (illustrative only):

- `id`
- `area_id`
- `code`
- `name`
- `description`
- `default_expected_duration`
- `is_external`
- `is_active`

Rules:

- Operation does not need a barcode when it is unambiguously resolved from Area configuration.
- If an Area supports multiple Operations, the workflow must resolve or confirm which Operation applies.

---

## 8.6 Machine

Represents a physical Machine or processing station.

Typical attributes (illustrative only):

- `id`
- `area_id`
- `name`
- `barcode_value`
- `description`
- `manufacturer`
- `model`
- `asset_tag`
- `serial_number`
- `installed_on`
- `notes`
- `maintenance` (explicit override: started timestamp, optional note, optional expected return date)
- `state_changed_at`
- `retired_on`

Asset metadata (manufacturer, model, asset tag, serial number, installed date, notes) is optional — production tracking never depends on it. The Asset Tag identifies the physical asset even when display names are reused across replacements.

Rules:

- Every Machine barcode must be unique.
- A Machine belongs to exactly one Area. The Area of an existing Machine is fixed — moving production capacity to another Area is a replacement (retire + new record), never an edit that would make history ambiguous.
- Machine assignment identifies the current executor.
- An Area without Machines involves no Machine assignment at all.
- Assignment behavior follows from the Area's Machines (§12): with Machines, quantity queues and is assigned through the explicit one-shot workflow (UI label `Assign to Machine`) — never by configuration and never by Machine count.
- The operational state is **derived**, never chosen by users: (1) an explicit Maintenance override wins; (2) otherwise assigned active quantity means Running; (3) otherwise the Machine is Idle.
- A `state_changed_at` timestamp records when the operational state last changed; every surface derives the visible elapsed time in state from it (compact formats such as `18m`, `1h 24m`, `2d 03h`, `<1m`). No formatted duration is ever stored.
- Maintenance may start while quantity remains assigned. Starting maintenance never moves, releases, completes, or transfers quantity and never rewrites history; it may carry an optional note and an optional expected return date. Clearing maintenance returns the Machine to Running when quantity is still assigned, otherwise to Idle.
- Retirement is blocked while active quantity is assigned — the quantity must first complete or transfer through the normal production workflow. A Machine ever referenced by Movement history is never hard-deleted; it is retired (operator wording: `Retired`, `Retire Machine`, `Retired on …`) and remains available for historical display and reporting only.
- A retired Machine never appears in assignment choices and accepts no new scans.
- Replacement follows §7 Machine: retire the old record, create a new record with its own stable identity and new barcode; the display name may be reused, the old record is never mutated.

---

## 8.7 QuantityFlow

Represents an internal tracking concept for active PN quantity.

Typical attributes (illustrative only):

- `id`
- `part_number_id`
- `quantity`
- `status`
- `route_mode`
- `assigned_route_id` (nullable)
- `parent_flow_id`
- `created_at`
- `closed_at`

Rules:

- Quantity Flow is internal tracking structure, not a labeled physical batch.
- A Quantity Flow may split into child flows.
- Multiple Quantity Flows of the same PN may merge.
- Splitting and merging must preserve full history.
- `route_mode` is `FLOATING` (default) or `PLANNED` (§7 Route and Route Mode); `assigned_route_id` is set only for `PLANNED` flows — a Floating flow has no AssignedRoute and its route trace is derived from Movement history.
- Current Position must be derived from Movement history.
- Quantity Flow must not be exposed to operators as unnecessary administrative complexity.

---

## 8.8 RouteTemplate

Represents a reusable production route definition. The user-facing name is **Planned Routes** (managed in Management → Planned Routes, §21) — clearly distinct from the Floating actual route traces derived from Movement history; the internal domain name stays `RouteTemplate`.

Typical attributes (illustrative only):

- `id`
- `name`
- `description`
- `archived_at`

A Route Template contains ordered Route Steps.

A Route may visit the same Area more than once.

Changing a Route Template must never retroactively alter active assigned Routes — edits apply to future assignments only; already released Quantity Flows keep their Assigned Route snapshots, and an in-production Assigned Route is changed separately in its own audited workflow with a reason (§8.10).

Lifecycle rules:

- A Route Template that has never been referenced by a released Quantity Flow may be deleted outright.
- A Route Template that has ever been used is archived instead of deleted: it stays visible in historical context but is never offered for new route assignments.
- There is **no separate template-versioning system**: existing Assigned Route snapshots preserve the historical route definitions, and actual immutable Movement history stays authoritative.

---

## 8.9 RouteStep

Represents one expected step in a Route.

Typical attributes (illustrative only):

- `id`
- `route_template_id`
- `sequence`
- `area_id`
- `operation_id`
- `expected_duration`
- `instructions`
- `preferred_machine_id`

Expected duration is advisory.

It may be used for:

- overdue indication,
- bottleneck detection,
- queue-time analysis,
- processing-time analysis,
- estimated completion.

Expected duration must never block production.

---

## 8.10 AssignedRoute

Represents a Route snapshot assigned to one **PLANNED** Quantity Flow. An AssignedRoute is optional: Floating flows (the default) have none.

Rules:

- It is copied from a Route Template.
- It becomes independent after assignment.
- Template changes must not alter active work.
- Authorized users may edit it.
- Different Quantity Flows of the same PN may have different Routes.
- Actual Movement history remains authoritative.
- Route deviations must be recorded.
- Route edits must preserve the previous route state in audit history.

---

## 8.11 PartMovement

Represents an immutable quantity event.

Typical attributes (illustrative only):

- `id`
- `part_number_id`
- `quantity_flow_id`
- `quantity`
- `movement_type`
- `from_area_id`
- `to_area_id`
- `source_machine_id`
- `destination_machine_id`
- `operation_id`
- `route_step_id`
- `scan_session_id`
- `worker_id`
- `station_id`
- `occurred_at`
- `server_received_at`
- `movement_reason`
- `reason`
- `reverses_movement_id`
- `device_event_id`
- `metadata`

Initial Movement types may include:

- `RECEIVED`
- `TRANSFERRED`
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE`
- `AREA_COMPLETED`
- `SPLIT`
- `MERGED`
- `STOCKED`
- `QUANTITY_ADJUSTED`
- `SCRAPPED`
- `ROUTE_ADJUSTED`
- `ROUTE_DEVIATION_CONFIRMED`
- `REVERSED`

`movement_reason` is a typed, optional movement intent; its first value is `REPAIR` — an explicit return of quantity to a previously visited Area to correct earlier work (`movement_type = TRANSFERRED`, `movement_reason = REPAIR`). Repair is chosen explicitly by the user, never inferred from the route history, and is not a Work Order Demand and not a Request Type. `reason` remains the free-text explanation and is required for Repair, Scrap, and quantity adjustments.

`QUANTITY_ADJUSTED` with `direction = INCREASE` records the intentional addition of physical quantity (operator-allowed, mandatory reason, auditable, never hidden as an ordinary transfer, never changing the requested quantity). `SCRAPPED` records damaged quantity removed from active production (§11 Quantity Model).

`AREA_COMPLETED` records Area Completion (§7 Area Completion): the selected quantity finished processing at its current Area and waits as `READY_TO_TRANSFER` on the finished rack. It clears the current Machine from the derived position while the Area remains the location, and it creates no route visit — completion happens inside the existing source Area. It is distinct from `RELEASED_FROM_MACHINE`, which returns **unfinished or paused** quantity from a Machine to the Area queue (`RELEASED_FROM_MACHINE → QUEUED`; `AREA_COMPLETED → READY_TO_TRANSFER`), and distinct from `STOCKED`, which alone represents manufacturing completion at the terminal Stockroom.

When quantity that is still actively processing (`ON_MACHINE`, or directly processing in an Area without Machines) is scanned into a different Area through a normal transfer, processing at the source Area is treated as completed: one **atomic application command** appends `AREA_COMPLETED` immediately followed by `TRANSFERRED` — either all required Movement records are written or none are. No separate prior manual DONE is required. Quantity already `READY_TO_TRANSFER` transfers with a normal `TRANSFERRED` alone. Undo of such a command reverses the complete command (§16).

Rules:

- Movement records PN quantity.
- Movement is not tied to a specific Work Order Allocation.
- Movement must never be silently overwritten.
- Corrections preserve the original event.
- Current state must be reconstructable from Movement history.

---

## 8.12 WorkOrderAllocation

Represents the assignment of stocked PN quantity to Work Order Demand.

Typical attributes (illustrative only):

- `id`
- `part_number_id`
- `work_order_demand_id`
- `quantity`
- `allocated_at`
- `allocated_by_worker_id`
- `allocation_reason`
- `is_manual_override`
- `reverses_allocation_id`

Rules:

- Allocation occurs only from stocked quantity.
- Allocation does not alter Movement history.
- Admin or Manager may adjust allocation at any time.
- Every adjustment must be auditable.
- Total active allocation must never exceed available stocked quantity.
- Allocation must never exceed remaining Work Order Demand unless explicitly authorized as a correction.

---

## 8.13 Worker

Represents an operator.

Typical attributes (illustrative only):

- `id`
- `employee_number`
- `name`
- `barcode_value`
- `is_active`

Worker identification is configurable per Area.

Supported modes:

- disabled,
- fixed Worker,
- scanned Worker Session.

Worker identity must not control production business rules.

---

# 9. Application Concepts

## ScanSession

ScanSession is temporary Application-layer state used to coordinate barcode scanning.

It may retain temporary context such as:

- Area
- active Worker
- expiration time

There is **no persistent Machine Session and no persistent PN intent**: every Scan Station production action is a one-shot dialog — scanning a Machine barcode only opens a one-shot assignment dialog with that Machine preselected, and completing or cancelling any dialog clears the temporary context. Only the Worker Session persists (§19).

ScanSession must never become the source of truth for production state.

---

# 10. Barcode Model

Barcode scanning is the primary interaction method.

Every barcode must identify its entity type deterministically.

Logical formats:

```text
PF:PN:<part-number>
PF:MACHINE:<stable-id>
PF:WORKER:<stable-id>
PF:AREA:<stable-id>
PF:SCRAP
```

The `PF:` namespace exists to identify PartFlow-owned barcodes, determine the entity type safely, and avoid confusing unrelated factory or vendor barcodes with PartFlow entities.

The PN barcode carries the PN itself. Parsing rules:

1. Trim scanner terminators and surrounding whitespace.
2. Require the exact `PF:PN:` prefix for scanned PN barcodes.
3. Treat the entire non-empty suffix as the PN.
4. Never parse PN segments and never validate a PN format.
5. Do not require the PN to exist in a preloaded catalog — the internal PartNumber record is created on first valid use (§8.1); ERP is not called during MVP.
6. Preserve the originally entered PN text for display; identity is case-insensitive (§8.1).

There are **no Action barcodes**. Action intent (Modify intake, Repair, Scrap, quantity addition) is selected through explicit one-shot dialogs, never through persistent armed barcode state. `PF:SCRAP` is a single dedicated, context-sensitive barcode: it is accepted only inside the Scrap workflow, where each scan increments a pending scrap counter (§11); scanning it in the main Scan Station input is rejected.

Requirements:

- Barcode values must be unique.
- Barcode identity must not depend on mutable display names.
- Raw ERP PN text must not automatically be treated as a PartFlow barcode.
- Manual PN entry must remain available and accepts any non-empty PN value.
- Barcode parsing must be deterministic.
- Unknown barcodes must be rejected clearly.
- Inactive entities must not accept production updates.

The PN barcode identifies only the PN.

It does not encode:

- Work Order,
- Work Order Demand,
- quantity,
- Job Number,
- Route,
- current Area,
- current Machine.

Those contexts are resolved by the application.

---

## Barcode Resolution

Operators must not select a scan mode before every scan.

The system should automatically identify barcode type.

When one valid context exists, the application may continue automatically.

When multiple valid contexts exist, the application must not guess.

It must present only relevant choices or request confirmation.

Examples of ambiguity include:

- multiple possible source Areas or Quantity Flows (never combined silently),
- multiple valid Operations in the Area,
- selecting between a normal transfer and an explicit Repair return to a previously visited Area,
- several plausible blank-number MODIFY Work Orders for the same PN (an explicit selection is required — never a guess),
- unexpected Route destination on a Planned Route.

No production write may occur until ambiguity is resolved.

---

# 11. Quantity Model

Quantity always represents physical parts.

A PN may be distributed across multiple Areas and Machines.

Example:

```text
PN ABC

Material        5
Cut             4
Lathe 1         3
Lathe 2         2
Mill            6
```

The sum of active quantity must remain reconcilable with:

```text
introduced quantity = active quantity + stocked quantity + scrapped quantity
```

where introduced quantity covers production releases, Modify intakes, and explicit `QUANTITY_ADJUSTED` additions. Scrap never reduces the Work Order Demand requested quantity.

---

## Quantity Splitting

Quantity may split whenever production requires.

Example:

```text
Material: 10
```

becomes:

```text
Cut: 4
Lathe: 6
```

Splitting:

- creates separate Quantity Flows,
- preserves the original PN,
- preserves quantity conservation,
- does not modify Work Order Demand.

---

## Quantity Merging

Quantity Flows of the same PN may later converge.

Example:

```text
Lathe 1: 2
Lathe 2: 4
```

becomes:

```text
Mill: 6
```

History must preserve how merged quantity arrived.

---

## Quantity Integrity

The application must never:

- create quantity accidentally,
- destroy quantity accidentally,
- duplicate quantity,
- lose quantity,
- produce negative quantity,
- move more than the available source quantity,
- stock more than the available quantity,
- allocate more than available stocked quantity.

Any intentional correction must create an explicit auditable event.

History must never be silently rewritten.

---

## Scrap

Damaged quantity is removed from active production with the canonical Movement type `SCRAPPED` (UI wording: “Scrap damaged quantity”).

The Scrap workflow:

- is entered explicitly from a PN action dialog;
- counts damaged pieces with the dedicated `PF:SCRAP` barcode — one scan increments the pending count by one, no production state changes while counting, and the pending count can be corrected or reset before confirmation;
- requires one common scrap reason;
- shows a final summary (PN, Area, Machine where applicable, original available quantity, scrap quantity, remaining active quantity, Worker, Scan Station, reason) before applying;
- on Cancel discards the pending count with no write;
- on Confirm creates **one** auditable `SCRAPPED` operation for the total quantity.

Scrapped quantity is displayed wherever a PN is presented operationally (Scan Station, Production Board, Area Board, Tracking), and Tracking shows the scrap history explicitly.

---

# 12. Processing Ownership Rules

## Area Without Machines

Example:

- Area: `External`
- Operation: `Plating`

An Area with zero Machines has no queue: it directly owns and processes received quantity.

When an operator scans PN quantity into the Area:

- the Area receives ownership,
- the quantity is considered actively processing,
- the configured Operation is recorded,
- Machine remains null.

The UI shows no placeholder Machine cards and no queued/on-machines statistics for such an Area.

If the Area supports multiple Operations, the applicable Operation must be resolved or confirmed.

---

## Area With Machines — `QUEUE_AND_ASSIGN`

An Area with one or more Machines always uses `QUEUE_AND_ASSIGN`. One Machine behaves exactly like several — behavior never differs by Machine count and quantity is **never auto-assigned** merely because the Area has a single Machine.

- Newly received quantity enters the Area queue.
- Quantity is assigned to a Machine through an explicit **one-shot assignment workflow** (scan the Machine as a shortcut, or start from the PN); there is no persistent Machine Session (§9).
- One Machine may hold quantities of multiple PNs.
- A queued PN row offers an `ASSIGN` action; a Machine-assigned PN row offers a `QUEUE` action that returns quantity to the Area queue (`RELEASED_FROM_MACHINE`).

The Machine remains the current executor until the quantity is:

- completed at the Area (`AREA_COMPLETED` — manual DONE or implicit completion during a transfer, §7 Area Completion),
- transferred,
- released back to the queue,
- reversed,
- or reassigned through an explicit event.

## Area Processing States

For an Area with Machines:

```text
QUEUED
  -> ASSIGNED_TO_MACHINE
ON_MACHINE
  -> AREA_COMPLETED
READY_TO_TRANSFER
  -> TRANSFERRED
next Area queue or direct processing
```

For an Area without Machines:

```text
PROCESSING
  -> AREA_COMPLETED
READY_TO_TRANSFER
  -> TRANSFERRED
next Area queue or direct processing
```

A Machine-assigned PN row offers two distinct actions that are never merged:

- `DONE` — complete processing and move the quantity to the finished state (`AREA_COMPLETED → READY_TO_TRANSFER`);
- `QUEUE` — return unfinished or paused quantity to the Area queue (`RELEASED_FROM_MACHINE → QUEUED`).

The manual DONE workflow identifies the PN, the current Area, and the current Machine when applicable; selects or confirms the quantity (MAX defaults to the quantity at the current source position); shows a dedicated confirmation view; records nothing before final confirmation; then appends one immutable `AREA_COMPLETED` event, derives `READY_TO_TRANSFER`, clears the current Machine, keeps the current Area as the physical location, and restores focus to the barcode input. An Area without Machines uses the same workflow without a Machine field. Partial completion is valid: only the selected quantity completes; the remaining quantity keeps its existing state (split semantics where required); every quantity of the PN is never marked DONE by one action.

Finished (`READY_TO_TRANSFER`) quantity is presented in the Area summary — Machine cards show only actively assigned quantity — and is never presented as stocked or manufacturing-complete.

---

# 13. Work Order Intake

Work Orders may enter PartFlow through:

- manual entry,
- file import,
- future ERP synchronization.

For a newly received Work Order:

1. Create or locate the Work Order.
2. Create or update Work Order Demand for each PN.
3. Locate the reusable PN folder.
4. Create the PN master and barcode if the PN is new.
5. Add Work Order Demand without creating a separate tracked PN.
6. Save the business demand. Saving Work Order Demand never automatically creates production quantity.
7. Confirm the Route Mode (Floating by default) — and the Planned Route where chosen — when production is released.

Production release is a separate, explicit action. On production release:

1. Confirm the release quantity.
2. Confirm the Route Mode — `FLOATING` (default, no Route required) or `PLANNED` with a selected Route.
3. Confirm the configured starting Area and Operation.
4. Create the Quantity Flow with its route mode.
5. Snapshot the Assigned Route **only** for a `PLANNED` flow — a Floating release has no AssignedRoute.
6. Append an immutable `RECEIVED` Part Movement.
7. Derive or update the current-position projection atomically with the Movement.

New ERP production normally uses Request Type `NEW`.

The starting Area may be Material or another configured starting Area.

If the PN already has active quantity, the system must show the existing distribution and require explicit confirmation of intent. A new Work Order requesting an already active PN never automatically creates additional physical quantity and never automatically merges Quantity Flows.

Work Order and Work Order Demand represent business demand; creating or editing Work Order Demand does not define current production position. Production release explicitly introduces physical quantity. Part Movement remains PN + Quantity Flow + quantity activity: Work Order Demand does not own shop-floor Movement, and Work Order Allocation remains separate from both.

## Source-System Mapping

When Work Order data originates from the external source system (manual entry from its records, file import, or future ERP synchronization), use this canonical mapping:

- Source Work Order Number → `WorkOrder.work_order_number`.
- Source Job Number → informational external Job Number associated with the relevant `WorkOrderDemand`.
- Source component Part Number → `PartNumber`.
- Source component row → candidate `WorkOrderDemand`.
- Source component Quantity → candidate `WorkOrderDemand.requested_quantity`, but only when the row represents production demand PartFlow is intended to track.
- Source Revision → the PartNumber's separate informational Revision field.

A source Work Order may also identify a parent or build-for assembly with its own Part Number, Description, and Revision. Treat that information as Work Order header context unless an approved workflow explicitly requires the parent assembly to become its own tracked WorkOrderDemand or QuantityFlow.

Never import every BOM component automatically as a tracked WorkOrderDemand: only rows representing production demand managed by PartFlow enter the tracking workflow. Source fields such as Type (`Fabricate`, `F`, `P`, `SM`, `Commercial`), Ref Designator, Attribute, Issued, Shelf, Cost, and Open/Closed carry no PartFlow business meaning: source Type values are not PartFlow Request Types, source BOM Quantity is not automatically requested quantity, Issued is not a Part Movement, Shelf is not an Area or current position, source Open/Closed is not a PartFlow status, and Cost is out of scope. There is exactly one Work Order concept — no second Work-Order-like aggregate may be introduced for source data.

## Work Order Demand Removal

A Work Order Demand line may be removed from its Work Order only while no production quantity has been released for it:

- An unsaved draft line may be removed immediately.
- A saved Work Order Demand with no released production quantity may be removed only after explicit confirmation.
- Once any quantity for a Work Order Demand has been released to production, that Work Order Demand must not be deleted from Work Orders. Later adjustments go through the correction and production workflows (§16); removal is not a correction mechanism.

Removing a Work Order Demand must never delete the PartNumber master, any Quantity Flow, any Part Movement, release history, or other Work Order Demand records for the same PN.

---

# 14. Modify Intake and Repair

## Modify Intake

`MODIFY` introduces physical quantity of a PN for modification work. A MODIFY intake may have no external Work Order Number, no predefined Route, and no existing active Work Order Demand — PartFlow still creates the minimum internal records needed for quantity integrity and traceability: an internal `WorkOrder` (`work_order_number = NULL`), one `WorkOrderDemand`, a `QuantityFlow`, and immutable Movement history.

When a scan opens the intake flow (PN has no active Work Order Demand):

1. Scan or enter the PN (created on first valid use, §8.1).
2. Confirm or change the Request Type — `MODIFY` is the **default, not a forced value**.
3. Confirm or change the Route Mode — `FLOATING` is the **default, not a forced value**; a Planned Route is selected only when `PLANNED` is chosen.
4. Confirm quantity, optional due date (owned by the WorkOrderDemand — the PN never owns a due date), and reason/notes where applicable.
5. Confirm the starting/current Area and, when needed, the Operation. `received_date` defaults to the scan timestamp.

On confirmation, the transaction creates or reuses the PartNumber, creates or reuses an applicable internal blank-number MODIFY Work Order, creates the WorkOrderDemand and QuantityFlow, records the initial Movement, establishes the current position, and places quantity in the Area queue (Area with Machines) or directly into Area processing (Area without Machines).

Work Order reuse must never guess: if the same PN has exactly one clearly applicable active blank-number MODIFY Work Order, reuse it; if multiple are plausible, an explicit selection dialog is required.

If the PN already has active quantity, the system must explicitly confirm whether the new quantity joins an existing Quantity Flow or creates a separate one. The system must never infer this from PN identity alone.

## Repair

`REPAIR` means some or all quantity already in production must return to a previously visited Area to correct work performed earlier:

```text
A → B → C → D → B
```

Rules:

- Repair does not create new physical quantity, is not a WorkOrderDemand, and is not a Request Type.
- Repair operates on an existing QuantityFlow: partial Repair splits the flow (depends on SPLIT); full-flow Repair moves the whole flow.
- Repair is recorded as a movement intent: `movement_type = TRANSFERRED`, `movement_reason = REPAIR` (§8.11) — no separate Repair aggregate.
- Returning to a previously visited Area must **never** automatically be assumed to be Repair — a previously visited Area may legitimately be the next normal production step, and a normal transfer there remains possible. The user explicitly chooses the Repair intent (UI wording: “Send quantity here for repair”, never “Create REPAIR demand”).
- The Repair workflow collects: source Area / source QuantityFlow, destination repair Area, repair quantity, a required reason, the affected PN, Worker, Scan Station, and timestamp, and ends with a summary confirmation that identifies the movement as a Repair movement.
- Actual Movement history remains authoritative; the Repair return extends a Floating Route's observed trace.

---

# 15. Core Scan Workflow

The normal workflow is keyboard- and scanner-first.

1. The Scan Station is bound to one Area (selected through the Station Selector, §21).
2. The operator scans a barcode (PN or Worker; a Machine barcode is a one-shot assignment shortcut).
3. The system identifies barcode type.
4. The system validates entity state and scan context.
5. The system resolves PN, source quantity, Operation, and Machine context; the quantity source is always explicit — with more than one valid source Area or QuantityFlow, the user selects exactly one (sources are never combined silently).
6. The system requests quantity only when necessary (transfer and assignment default to the available MAX; quantity additions have no default and no MAX).
7. The system requests confirmation only when ambiguity or deviation exists; every completed action shows its summary before Confirm.
8. The system records an immutable Movement.
9. The system derives the new production state.
10. The Area view and dashboards refresh immediately.
11. Barcode input regains focus and the temporary dialog context is cleared — nothing stays armed.

A transfer whose source quantity is still actively processing implicitly completes that processing: the confirmation identifies the completion, and one atomic command appends `AREA_COMPLETED` then `TRANSFERRED` (§8.11). Repair remains an explicit movement intent — it is never inferred merely because a previously visited Area appears again, and source ambiguity rules are unchanged: multiple possible Quantity Flows or source positions require explicit selection and are never combined silently.

Scan Station actions are **one-shot**:

- **Machine-first:** scanning a Machine barcode opens a one-shot assignment dialog (UI label `Assign to Machine`) with the Machine preselected; the operator selects or scans the PN, enters the quantity (MAX default), reviews the summary, and confirms. The assignment applies once — the next scan starts fresh; it never creates a sticky Machine Session.
- **PN-first:** scanning a PN barcode opens the applicable one-shot dialog — intake when the PN has no active demand (§14), source-explicit transfer when the quantity is elsewhere, or an action dialog with only the currently valid choices (assign queued quantity, receive more, add quantity, complete processing — DONE in a direct-processing Area, Repair, Scrap) when the PN already has quantity in the Area. Machine-assigned quantity is completed through the Machine-card row's `DONE` action (§12).
- Completing or cancelling a dialog clears the pending context; Cancel always means no write.

The system must clearly reject, with no write:

- unknown barcodes,
- inactive entities,
- invalid Area/Machine combinations,
- impossible quantities,
- quantity exceeding available source quantity,
- unauthorized corrections,
- route deviations without required confirmation.

An ambiguous PN context is not simply rejected. When multiple valid contexts exist, the system must present the relevant choices and require explicit confirmation; unresolved ambiguity blocks the write, and nothing is recorded until one choice is confirmed. Cancelling abandons the pending intent with no write.

A duplicate transport retry carrying the same event id must return the original idempotent result and must not create another Movement.

A scan is successful only after the server confirms the recorded write. Connectivity status shown in the UI is an early warning, never permission to record a Movement optimistically. If connectivity disappears between the last successful connectivity check and a write request, the request must fail as "nothing recorded"; the UI must never display a false recorded result.

---

## Scan Station Persistence

A Scan Station's identity and its binding to one Area are stable application and infrastructure configuration. The Scan Station UI is addressed per station (`/scan-station/<station-id>`); the bare Scan Station route shows a Station Selector and never auto-selects or silently substitutes a station. A database table such as `scan_stations` is permitted; Scan Station configuration is not required to be a core domain aggregate.

ScanSession remains temporary context. Neither Scan Station configuration nor ScanSession is the source of truth for production state — that remains the immutable Part Movement history. Part Movement records the stable station identity (`station_id`) for audit.

---

# 16. Undo and Correction

Undo exists to correct recent scanning mistakes.

Undo must not delete the original Movement.

Instead, the system must:

- create a compensating or reversal Movement,
- reference the original Movement,
- restore derived quantity state,
- record Worker and timestamp,
- require a reason when configured.

Undo targets the most recent eligible **completed PN operation** and always shows a summary confirmation first (PN, original action, quantity, source and destination, Machine where applicable, Worker, timestamp, and the effect of the reversal); Cancel performs no write. After a confirmed Undo the “last scanned PN” context advances to the next eligible previous operation; Undo disables when nothing is eligible.

Production Undo must operate on the complete **application command**: when one user action created multiple related Movement records — for example a transfer that implicitly completed source processing (`AREA_COMPLETED` + `TRANSFERRED`, §8.11) — the reversal compensates the whole command rather than blindly reversing one arbitrary row.

Operators may undo only recent eligible actions when authorized.

Managers and Admins may perform broader corrections.

All corrections must remain auditable.

---

# 17. Production Routing

Every Quantity Flow has a route mode (§7 Route and Route Mode): `FLOATING` by default, `PLANNED` when guidance is wanted.

## Floating Route (default)

A Floating Route has no predefined sequence and no AssignedRoute snapshot. The actual route is **derived from immutable Movement history**: each visited Area extends the observed trace, repeated Areas are preserved (including Repair returns), different Quantity Flows of the same PN may have different traces, and split flows continue independently. Tracking derives and displays the Floating Route trace from Movement history; no second mutable route history duplicates PartMovement.

## Planned Route

A Planned Route describes the expected manufacturing path of a Quantity Flow.

Planned Routes provide planning and tracking guidance.

They do not override actual Movement history.

---

## Route Template

A Route Template is a reusable sequence of Route Steps, presented to users as a **Planned Route** and managed in Management → Planned Routes (§21). Ever-used templates are archived rather than deleted; archived templates remain visible in historical context but are never offered for new route assignments (§8.8).

Example:

```text
Material
→ Cut
→ Lathe
→ Deburr
→ External
→ Stockroom
```

A Route may visit the same Area more than once.

Example:

```text
Material
→ Mill
→ External
→ Mill
→ Stockroom
```

---

## Assigned Route

When production of a `PLANNED` flow begins, a Route snapshot is assigned to the Quantity Flow. Floating flows have no Assigned Route.

Rules:

- Different Quantity Flows of the same PN may have different Routes.
- A split may inherit the parent Route or receive a modified Route.
- Route Template changes must not affect active assigned Routes.
- Authorized edits affect only the selected Quantity Flow.
- Actual Movement history remains the source of truth.

---

## Route Deviation

Route deviation applies to Planned Routes only (a Floating Route has no expectation to deviate from). When quantity reaches an unexpected Area:

1. Warn the operator.
2. Require confirmation when configured.
3. Record the actual Movement.
4. Record the deviation.
5. Preserve the previous Route.
6. Update the assigned Route when authorized and appropriate.
7. Record user, timestamp, and reason.

The application must represent actual production rather than forcing production to match an obsolete plan.

---

## Expected Duration

Each Route Step may define expected processing duration.

Expected duration may support:

- Days Left,
- Total Days,
- overdue indication,
- queue-time analysis,
- processing-time analysis,
- bottleneck visibility,
- estimated completion.

Expected duration is advisory and must never block production.

---

# 18. Stockroom and Completion Allocation

Stockroom is the normal terminal Area.

When quantity is scanned into Stockroom:

- a `STOCKED` Movement is recorded,
- the quantity is considered manufacturing-complete,
- the quantity becomes available for Work Order Allocation.

Production Movement and Work Order Allocation remain separate.

---

## Allocation Order

The system must suggest allocation using the canonical demand ordering:

1. Highest manager-defined Work Order Demand priority (Hot rank).
2. Within the same priority level:
   - demand **with** a due date comes first, earliest due date first;
   - demand **without** a due date comes after all dated demand;
   - undated demand is ordered by the parent Work Order's `received_date`, oldest first.

Equal values are resolved by a stable deterministic tie-breaker such as Work Order Demand creation order or internal ID. The tie-breaker is an implementation detail, not a business rule.

This canonical demand ordering applies wherever due-date ordering appears — allocation suggestion, work ordering, and demand-sorted displays.

---

## Receiving Confirmation

The normal Stockroom workflow should be:

1. Scan PN.
2. Enter or confirm completed quantity.
3. Review suggested Work Order Allocation.
4. Confirm the allocation.

The suggestion must show:

- affected Work Order,
- requested quantity,
- previously allocated quantity,
- remaining shortage,
- proposed quantity.

Routine receiving should not require Manager approval.

Operators may review and adjust the suggested Work Order Allocation before confirmation.

Admin and Manager may adjust Work Order Allocation at any time.

Every change must remain auditable.

The total active allocation must equal the portion of stocked quantity being allocated and must never exceed available stocked quantity.

---

## Work Order Completion

A Work Order is complete when all of its Work Order Demand records are fully allocated.

When complete:

- the Work Order leaves active production views,
- the Work Order remains available in History,
- Movement history remains unchanged,
- later work must be represented by new Work Order Demand or new internal demand rather than reopening historical Movement.

---

# 19. Worker Sessions

Worker identification is configurable per Area.

Supported modes:

- no Worker identification,
- fixed Worker,
- Worker barcode session.

When Worker scanning is enabled:

- scanning a Worker barcode activates that Worker,
- subsequent scans use the active Worker,
- a Worker scan never replaces the last-scanned-PN context,
- the session ends when another Worker signs in, the Worker signs out, or the session expires.

Worker identity is accountability metadata and must never determine business correctness.

There is **no Machine session** of any kind: Machine selection is always part of a one-shot assignment action (§12, §15). The active Worker must always be visible on the Scan Station.

Session state reduces repetitive scanning but must never replace persistent Movement history.

---

# 20. Roles and Permissions

PartFlow uses role-based authorization.

Machines and Route Templates (Planned Routes) are production master data — operational management functions, not system administration. Managing them is **permission-based**: an authorized production specialist — for example a Production Manager, Process Engineer, or Maintenance Manager — may manage Machines and Route Templates without being an Administrator. Administrators retain these capabilities, but they are not Administrator-exclusive, and Administration keeps no duplicate Machines or Route Templates screens (§21).

## Administrator

Administrator capabilities include:

- manage Departments,
- manage Areas,
- manage Operations,
- manage Machines (shared with authorized production roles — managed through Management → Machines, §21),
- manage Workers,
- manage users and roles,
- manage Scan Stations,
- manage barcode configuration,
- manage Route Templates (shared with authorized production roles — managed through Management → Planned Routes, §21),
- manage scan behavior,
- manage Worker session policies,
- manage correction permissions,
- edit Work Order Demand,
- edit Work Order Allocation,
- perform authorized historical corrections,
- configure system settings.

---

## Manager

Manager capabilities include:

- view all current and historical production data,
- create and edit Work Orders,
- edit Work Order Demand,
- set Work Order Demand priority,
- reorder Hot items,
- assign and edit Routes,
- perform quantity corrections,
- edit Work Order Allocation,
- resolve exceptional production situations,
- export and print reports.

---

## Operator

Operator capabilities may include:

- scan PN barcodes,
- scan Machine barcodes,
- scan Worker barcodes,
- receive quantity into an Area,
- assign quantity to a Machine,
- confirm quantity,
- complete production into Stockroom,
- confirm suggested allocation,
- review and adjust suggested completion allocation,
- undo recent eligible scans.

Operators must never directly rewrite historical data.

---

# 21. Application Views

## Scan Station

The Scan Station is a fixed production interface assigned to one Area. Stations are addressed per URL: the bare Scan Station route shows a Station Selector (Station ID, Department, Area, supported Operations, whether the Area has Machines) and never auto-redirects; an unknown or inactive Station ID is an explicit error, never a silent fallback. The Station ID stays a faint non-interactive footer caption (alongside the mode label and the `Ctrl+Shift+K: switch mode` hint); switching stations goes through the Station Selector URL, never through the footer.

Requirements:

- one-screen normal workflow,
- no normal navigation,
- large focused barcode input directly under the header, spanning the full width (PN and Worker barcodes; Machine barcode only as a one-shot assignment shortcut; no Action barcodes),
- automatic input refocus,
- immediate validation feedback,
- visible Department, Area identity/color, and Operations,
- visible Area statistics in the header, the single Area summary surface (Areas with Machines: Total PNs, Total pcs, Queued, On machines, Done, Hot — reconciling as Total pcs = Queued + On machines + Done; Areas without Machines: Total PNs, Total pcs, Processing, Done, Hot — reconciling as Total pcs = Processing + Done; semantic number tones: Queued warning, On machines/Processing information, Done success, Hot error),
- visible active Worker,
- visible last scanned PN inside the scan card, with the Undo action anchored at its right edge (only completed PN operations become the last scanned PN; Worker scans and cancelled dialogs never replace it),
- quantity entry only when required,
- current Area quantity in the shared Area/Machine monitoring layout (§ Area Board) — the `In this Area now` card left, Machine cards in a right-side grid that wraps within itself; no Machine region for Areas without Machines; Machine cards show the derived operational state with its elapsed time in state (`running · 1h 24m`, derived from the state-change timestamp — §8.6) and, under maintenance, the maintenance note and expected return date,
- separate on-Machine, queued, and finished (`Finished — ready to move`) quantity (Areas with Machines) or direct processing and finished groups (Areas without Machines) — finished quantity belongs to the Area summary and Machine cards show only actively assigned quantity,
- Machine-card PN rows with the two distinct actions `DONE` and `QUEUE` (§12),
- authorized Undo with a summary confirmation (§16),
- manual entry fallback accepting any non-empty PN value.

There is no persistent Machine Session, no pending armed context, and no Recent Scans list.

---

## Production Board

The Production Board is a read-only large-screen Department display.

Requirements:

- readable from a distance,
- automatic pagination,
- automatic page rotation,
- dynamic rows per page,
- priority and due-date sorting following the canonical demand ordering (§18),
- overdue highlighting,
- Area color display,
- distributed PN quantity display,
- time in current Area or Machine shown per distributed quantity,
- clear scrapped/damaged quantity visibility without making the board unreadable (scrapped quantity has its own layout space and never overlaps other fields),
- explicit Area and Machine presentation data: actively Machine-assigned quantity shows the Machine as a compact chip with the full state wording `on machine`; finished quantity shows the current Area with a `done`/`ready` state, never `on machine` and never the Machine as current executor,
- a blank external Work Order Number rendered as `—`,
- Part Number rendered on a single line.

Suggested columns:

| No. | Part Number | Areas and Quantities · Time | Due Date | Total Days | Job Numbers |
|---|---|---|---|---|---|

Days Left is displayed inside the Due Date column as a highlighted secondary line rather than as a separate column.

Example distribution with time in location:

```text
Cut (3 · 3h 40m), Lathe 1 (4 · 2h 05m), Lathe 2 (2 · 1h 10m), Mill (6 · 45m)
```

Time in location may be highlighted when it exceeds the expected duration of the active Route Step.

---

## Area Board

The Area Board provides a focused view of production currently owned by one Area. Its per-Area detail uses the same shared Area/Machine monitoring layout and presentation components as the Scan Station (`In this Area now` summary card left, Machine monitoring cards in the right-side grid; the summary card spans the full width for Areas without Machines) — read-only, without the Scan Station action buttons, and without visual drift between the two views.

It should show:

- PN,
- total quantity,
- Area queue,
- Operation,
- Machine distribution,
- finished (`READY_TO_TRANSFER`) quantity, distinguished from queued, on-Machine, and directly processing quantity — shown in the Area summary, never inside a Machine card and never as Stocked,
- associated active Work Orders,
- Job Numbers,
- due dates,
- priority,
- time in Area,
- scrapped quantity in the PN summaries,
- search and sorting.

---

## Manager Summary

The Manager Summary provides an operational overview grouped by Area.

Each Area should show:

- Area name and description,
- supported Operations,
- total physical quantity,
- queued quantity,
- Machine assignments,
- PN list,
- priority,
- due date,
- search and sorting.

The layout may scroll horizontally when all Areas do not fit.

---

## Machines

Machines is the management view for Machine lifecycle, maintenance, and asset identification — production master data managed by authorized production roles (§20), grouped under Management alongside Area Board, Tracking, Work Orders, Planned Routes, and Priority. Running and idle are derived from assigned quantity; maintenance is the only state set by hand (§8.6).

It must provide:

- a table of active Machines: identity (display name and Area), derived operational state with the elapsed time in state, currently assigned PN portions with quantities, and asset metadata (asset tag, manufacturer, model),
- per-Machine actions: start maintenance (optional note, optional expected return date), clear maintenance, edit, and retire,
- a separate Retired Machines table: name, `Retired on YYYY-MM-DD`, asset metadata, notes — historical display and reporting only, with no actions,
- a New Machine / Edit Machine dialog: display name and barcode value (both required; barcode unique), Area (selectable for a new Machine, fixed for an existing one — §8.6), and optional manufacturer, model, asset tag, serial number, installed date, and notes,
- retirement blocked while active quantity is assigned, with the quantity completing or transferring through the normal production workflow first,
- replacement guidance following §7 Machine: retire the old record, create a new record with its own identity and new barcode; the display name may be reused.

Machine management stays focused on lifecycle, maintenance, and asset identification. PartFlow is not a CMMS: no spare parts, maintenance schedules, service contracts, or cost accounting.

---

## Tracking

Tracking is the primary management interface.

Managers must be able to search and filter by:

- PN,
- Work Order,
- Job Number,
- Area,
- Operation,
- Machine,
- Request Type,
- priority,
- status,
- due date.

The selected PN view must show:

- PN master data,
- barcode,
- image and revision,
- active Work Order Demand,
- requested quantity by Work Order,
- allocated quantity by Work Order,
- remaining shortage by Work Order,
- current quantity by Area,
- current Machine assignments,
- finished (`READY_TO_TRANSFER`) quantity per Area, distinct from active Machine assignment, direct processing, and `STOCKED` — with `AREA_COMPLETED` visible as an immutable Movement in history and operator wording such as `Completed processing at Lathe — ready to transfer` in summaries; the finished rack never appears as a route step,
- Quantity Flows with their route mode,
- Planned Routes and Floating actual route traces,
- actual Movement history,
- scrap history and cumulative scrapped quantity,
- time in each Area,
- stocked quantity,
- Allocation history,
- correction history,
- the `(archived)` marker on archived/soft-deleted PNs (original PN text preserved),
- a blank external Work Order Number as `—`.

Route visualization supports both modes. For Planned Routes it must distinguish completed, active, queued, and future steps plus deviations. For Floating Routes it derives the actual trace from Movement history, shows repeated Areas, shows split flows independently, and marks Repair transfers explicitly, for example:

```text
A → B → C → D → B
                ⟲ REPAIR
```

It must not imply that the entire PN is at one Route Step.

---

## Work Orders

Work Orders is the management view for manual Work Order entry (the Work Order Intake workflow, §12). It is a light-theme management view.

The view must support the minimum confirmed workflow:

1. Create or locate a Work Order.
2. Add or update one or more Work Order Demand records.
3. Locate or create the PartNumber.
4. Create the PN barcode when the PN is new.
5. Enter: Work Order Number (optional — a blank number is saved as NULL on an internal Work Order and displays as `—`, §7 Work Order), received date (defaults to the current date), PN (any non-empty PN text — created on first use, §8.1), Request Type (default `NEW`), requested quantity, due date (optional — a missing due date is valid data, §8.3), priority when applicable, external Job Numbers, and requester, reason, and notes when applicable.
6. Save business demand without automatically creating production quantity.
7. Provide a separate explicit `Release to production` action following the release steps in §12.

On production release the view must confirm release quantity, Route Mode (Floating by default; a Route only for Planned), and the configured starting Area and Operation, and show the resulting Quantity Flow, route mode, Area, quantity, and `RECEIVED` Movement.

If the PN already has active quantity, the view must show the existing distribution and require explicit confirmation of intent; it must never automatically create additional physical quantity or merge Quantity Flows.

Work Order Intake must not grow into ERP-style customer, pricing, invoicing, shipping, purchasing, or accounting functionality.

---

## Planned Routes

Planned Routes is the management view for reusable route definitions (`RouteTemplate`, §8.8) — production master data managed by authorized production roles (§20) within the Management grouping. The name keeps templates clearly apart from the Floating actual route traces shown in Tracking.

It must provide:

- a searchable list of route templates: name and description, the compact ordered Area step sequence, status — Active (with updated date) or Archived (with archived date, visually quiet),
- usage per template: the Quantity Flows released with it (flow id, PN, released date), or `Never used`,
- actions: edit, duplicate, and archive for ever-used templates; delete only for never-used templates; archived templates offer only duplicate,
- a create/edit dialog: name, description, and ordered steps (Area, Operation, advisory expected duration, optional preferred Machine, optional instructions) with explicit reordering and step add/remove,
- an explicit note when editing a used template: changes apply to future assignments only; already released Quantity Flows keep their Assigned Route snapshots, and an in-production Assigned Route is changed separately in its own audited workflow with a reason (Tracking → Edit assigned Route),
- archived templates visible in historical context but never offered for new route assignments.

There is no separate template-versioning system — Assigned Route snapshots preserve historical definitions, and Planned Routes guide production without ever replacing or rewriting actual Movement history.

---

## Priority Management

Priority belongs to Work Order Demand.

The Hot list is managed within the Department:

1. Show Hot Work Order Demand sorted by explicit priority rank.
2. Add Work Order Demand to the Hot list by searching and selecting, or by scanning the PN barcode.
3. If a PN has multiple active Work Order Demand records, each Work Order Demand is selected and ranked separately.
4. Add new Hot entries at the bottom by default; adding at the bottom applies directly.
5. Allow drag-and-drop reordering.
6. Allow removing an entry from the Hot list only after an explicit confirmation that identifies the PN and Work Order Demand; cancelling changes nothing. After confirmation the remaining ranks close the gap.
7. Require explicit confirmation before applying any operation that changes the order of existing Hot entries — drag-and-drop, Move Up, Move Down, Undo, and Redo. The confirmation identifies the affected PN and Work Order Demand, the previous rank, the proposed new rank, and the action type; cancelling leaves the list and both histories unchanged, and the visible list is never renumbered before confirmation.
8. Apply every confirmed Hot list change and record it in the audit trail.
9. Provide Undo and Redo for recent Hot list changes instead of a separate save-or-cancel step; stepping back or forward is itself an order change and requires the same confirmation.
10. Use the stored rank as the highest work and allocation priority.

Multiple Work Orders requesting the same PN may have different priorities.

---

## Administration

Administration stays focused on system administration:

- Departments,
- Areas,
- Operations,
- Scan Stations,
- Workers,
- PartNumber maintenance (archive/soft-delete, separate explicit purge — §28 Administrative Archival and Purge),
- users,
- roles,
- permissions,
- barcode configuration,
- scan behavior,
- Worker policies,
- history archival and purge maintenance with retention settings (§28 Administrative Archival and Purge),
- application settings.

Machines and Route Templates are not Administration screens: they are production master data managed permission-based in Management → Machines and Management → Planned Routes (§20) with no duplicate Administration screens. Machines remain part of the minimum environment setup prerequisite — configured through Management → Machines before real production runs.

Administrative workflows must remain separate from normal production scanning; retention settings belong in Administration/configuration, never in production workflow logic.

---

# 22. Department Scope

The initial deployment targets Machine Shop.

Future Departments may include:

- Purchasing,
- Assembly,
- Production,
- Outsourcing,
- Stockroom,
- Quality Control.

Core domain logic must not hard-code Machine Shop assumptions.

Department configuration may define:

- Areas,
- Operations,
- Machines,
- Routes,
- starting Area,
- terminal Area,
- scan behavior,
- Worker requirements,
- display settings.

---

# 23. ERP Boundary

ERP owns business planning and source master data.

PartFlow owns production tracking.

Rules:

- PartFlow must work without ERP connectivity.
- ERP IDs must remain separate from internal IDs.
- ERP imports should be idempotent.
- ERP response models must not leak into domain logic.
- PN and Work Order Number formats must never be assumed.
- Production history is owned by PartFlow.
- ERP changes must not erase local Movement history.
- Modify intake and Repair movements remain valid PartFlow concepts even when ERP does not model them.

---

# 24. Application Architecture

Preferred stack:

- React or Next.js
- FastAPI
- PostgreSQL

Architecture:

```text
Presentation
    ↓
Application
    ↓
Domain
    ↓
Infrastructure
```

Rules:

- Keep routes and controllers thin.
- Put scan orchestration in the Application layer.
- Keep business rules in Domain and Application.
- Domain must not depend on frameworks.
- Infrastructure must not control business flow.
- Use transactions for scan writes.
- Use database constraints for identity and quantity integrity.
- Preserve immutable Movement history.
- Avoid unnecessary global state.
- Keep ERP integration isolated.

---

# 25. Transaction Requirements

Every production write must be atomic.

A scan transaction may include:

1. Parse barcode.
2. Validate entity state.
3. Validate permission.
4. Resolve ScanSession context (Worker) and the one-shot dialog input.
5. Resolve source Quantity Flow (explicitly selected when several are valid).
6. Validate available quantity.
7. Validate Area, Operation, Machine, and Route.
8. Record Movement.
9. Update or rebuild derived current state.
10. Update projections.
11. Commit.

If any required step fails, the entire transaction must roll back.

The system must never partially record a production Movement.

---

# 26. Logging

Logs must answer:

- What happened?
- Which PN?
- Which quantity and Quantity Flow?
- Which Work Order Demand was relevant, if any?
- Which Area?
- Which Operation?
- Which Machine?
- Which Worker?
- Which Scan Station?
- Why did it fail?
- Was the action reversed or corrected?

Avoid noisy or duplicated logs.

Do not expose raw internal exceptions to operators.

---

# 27. Reporting

The system should support:

- active Work Order status,
- active Work Order Demand,
- active PN status,
- quantity by Area,
- quantity by Machine,
- Area queues,
- overdue demand,
- Hot and priority demand,
- assigned Route versus actual path,
- Movement history,
- time in Area,
- time at Machine,
- stocked quantity,
- Work Order Allocation,
- completed Work Order history,
- route deviation history,
- correction history.

Reports must distinguish:

- business demand,
- production quantity state,
- Movement history,
- completion Allocation.

---

# 28. Audit and Data Integrity

The system must preserve a complete audit trail for:

- Work Order creation and edits,
- Work Order Demand creation and edits,
- priority changes,
- Route assignment,
- Route modification,
- quantity splits and merges,
- Area transfers,
- Machine assignments,
- Stockroom completion,
- Work Order Allocation,
- Allocation corrections,
- quantity adjustments (including auditable quantity additions),
- Repair movements,
- Scrap operations,
- Undo,
- Worker Sessions,
- administrative configuration changes,
- administrative archival and purge operations (who, when, scope, reason).

Historical production records never disappear through normal application workflows — normal production runtime is append-only (**immutable during production runtime**).

Database constraints should enforce, whenever practical:

- unique PN,
- unique Work Order Number where required by business rules,
- unique barcode values,
- unique Machine-to-Area relationships,
- non-negative quantities,
- valid Area/Machine relationships,
- allocation not exceeding available stock,
- idempotent scan-submission event identifiers (`device_event_id`).

---

## Administrative Archival and Purge

Normal production runtime is append-only and never silently rewrites or deletes history. Separately, Administrators hold **explicit maintenance authority in every environment** (**explicit Admin archival/purge maintenance**). Archival is always preferred over destructive deletion.

**PartNumber archival/deletion.** Admin may remove junk or test PartNumber records:

- the default administrative “delete” archives (soft-deletes) the PN;
- archived PartNumbers disappear from normal active lookup and intake;
- historical displays retain the original PN text; references display the PN with a clear `(archived)` or `(deleted)` marker;
- no cascading delete may destroy unrelated tracking history; a PN text snapshot is stored or preserved where required for historical rendering;
- archived records remain available to Admin history/audit tools;
- a physical purge may exist as a separate, explicitly named maintenance operation — never as the normal delete button.

**History archival and purge.** Admin may archive or purge history in every environment through explicit maintenance workflows, triggered by an Admin-configured periodic retention policy, an Admin-configured maximum data-size threshold, or a manual Admin request. Rules:

- normal application workflows cannot delete history;
- maintenance operations require explicit Admin authorization, show a scope/impact preview before execution, require a reason, and record who initiated the operation and when;
- archive is the default and preferred behavior; physical purge is separate and more explicit;
- the operation never silently cascades into active production state — active quantity and current projections must remain reconcilable;
- archived history remains queryable through an Admin archive view or export path where practical;
- retention settings live in Administration/configuration, not in production workflow logic.

---

# 29. Initial Scope

The initial release should support:

- Machine Shop,
- PN master records,
- reusable PN folder barcode,
- manual Work Order entry,
- file-based Work Order import,
- Work Order Demand,
- Modify intake,
- explicit Repair movements,
- Scrap,
- Areas,
- multiple Operations per Area,
- optional Machines,
- barcode scanning,
- quantity distribution,
- quantity splitting and merging,
- Quantity Flow routes,
- Stockroom completion,
- suggested Work Order Allocation,
- manual Allocation adjustment by Admin and Manager,
- optional Worker identification,
- Scan Station,
- Work Orders,
- Production Board,
- Area Board,
- Manager Summary,
- Tracking,
- Machines management,
- Planned Routes management,
- immutable Movement history,
- Undo,
- role-based authorization.

ERP synchronization may be added later without replacing the core model.

---

# 30. Future Scope

The following are outside the confirmed initial scope unless separately approved:

- individual piece tracking,
- full ERP synchronization,
- machine automation,
- CNC telemetry,
- production scheduling,
- inventory valuation,
- cost accounting,
- payroll,
- IoT integration,
- predictive analytics,
- full quality management,
- general workflow engine,
- offline scan synchronization.

Future features must extend the existing PN, quantity, Movement, Route, and Allocation model rather than replace it.

---

# 31. Explicit Non-Goals

PartFlow must not become:

- a full ERP,
- a full MES,
- an accounting system,
- an individually serialized part tracker,
- a CNC controller,
- a machine telemetry platform,
- an inventory valuation system,
- an automatic ERP replacement.

Do not introduce these responsibilities without an explicit project decision.

---

# 32. Remaining Open Decisions

Only the following unresolved decisions remain:

1. Whether stocked quantity may be returned to active production through a controlled reversal.
2. The exact expiration rules for Worker sessions.
3. Whether offline scan synchronization will be included in a later release.

(The former scrap question is resolved: `SCRAPPED` is a first-class Movement type, §8.11/§11. Machine sessions no longer exist, so no expiration rule applies to them.)

Implementations must avoid assumptions that make these decisions difficult to change.

---

# 33. Guiding Principles

Every future feature must reinforce these principles:

- Track PN identity.
- Track physical quantity, not individual pieces.
- Keep Work Order Demand separate from production Movement.
- Keep Work Order Allocation separate from production Movement.
- Default to Floating Routes; assign Planned Route snapshots only where guidance is wanted.
- Derive current production state — and Floating Route traces — from immutable Movement history.
- Preserve quantity integrity.
- Prefer scanner-first workflows.
- Minimize operator interaction.
- Require confirmation when intent is ambiguous.
- Let production reality override outdated plans.
- Remain ERP-independent.
- Preserve complete auditability.
- Prefer the simplest design that accurately represents the shop floor.
