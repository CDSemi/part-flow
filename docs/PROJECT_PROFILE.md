# PartFlow Project Profile v6

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
- Which Purchase Orders request that Part Number.
- How many pieces each Purchase Order requires.
- What production work remains.
- Which route each active quantity is expected to follow.
- Which Areas and Machines each quantity has passed through.
- Whether requested quantities have been completed and stocked.
- How completed quantities are allocated to Purchase Orders.

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

The system does not use `PO + PN`, a physical batch, or an individual piece as the primary tracked object.

Instead:

- PN identifies the reusable part definition.
- Physical quantity represents production state.
- PO Demand represents business demand.
- Part Movement represents production activity.
- PO Allocation connects stocked quantity to individual PO Demand only after completion.

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

- belongs to the PN, not to a specific PO,
- is reused across all POs requesting that PN,
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
- may be requested by multiple active POs,
- may have multiple external Job Numbers (used only for display, searching, sorting, and reporting),
- may have quantities in multiple Areas simultaneously,
- may have quantities assigned to multiple Machines simultaneously,
- may have different quantity flows following different Routes,
- must remain permanently traceable after first recording.

The system does not track:

- individual serial pieces,
- a physical batch as a first-class identity,
- `PO + PN` as the movement identity.

Instead:

- Movement is recorded at PN + quantity level.
- PO Demand is maintained separately.
- Stocked quantity is allocated to PO Demand after completion.
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
9. PO Demand and shop-floor Movement are separate concepts.
10. PO Allocation and shop-floor Movement are separate concepts.
11. PO Allocation may change without rewriting Movement history.
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
- remains the same across multiple POs,
- is represented by the reusable folder barcode.

---

## Purchase Order

A business order containing one or more requested PNs and quantities.

Abbreviation: `PO`.

A PO Number:

- originates externally,
- has no fixed format,
- must be treated as an arbitrary string.

---

## PO Demand

The requested physical quantity of one PN for one PO.

PO Demand contains the business context needed to answer:

- which PO requests the PN,
- requested quantity,
- allocated quantity,
- remaining shortage,
- due date,
- priority,
- request type,
- external Job Numbers (used only for display, searching, sorting, and reporting).

PO Demand does not define the current production location.

---

## Request Type

Describes why a PO Demand or internal demand exists.

Initial values:

- `NEW`
- `REWORK`
- `MODIFY`

Rework and Modify are internal PartFlow concepts and do not need to exist in ERP.

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

## Route

The ordered production path expected for a Quantity Flow.

A Route may contain:

- Areas,
- Operations,
- expected durations,
- instructions,
- preferred Machines.

A Route is guidance. Actual Movement history remains authoritative.

---

## Part Movement

An immutable event recording the movement, assignment, completion, correction, split, or merge of PN quantity.

---

## Current Position

The derived current Area and optional Machine holding a Quantity Flow.

---

## PO Allocation

The assignment of stocked PN quantity to specific PO Demand records.

PO Allocation is independent from Part Movement.

---

## Priority

A manager-defined rank stored on PO Demand.

Priority is the highest allocation and work-ordering criterion.

---

## Hot Part

A UI and business label indicating expedited PO Demand.

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

- `part_number` must be unique.
- `part_number` must be treated as an arbitrary string.
- Real PN values are commonly multi-segment hyphenated numeric strings of varying length (shapes such as `214-406`, `78-04-0031`, `0455-20-0118-03`, `2027-60-8114-00`). The exact string must be preserved everywhere; PN segments must never be parsed for business meaning.
- `name`/`description` are free text as supplied — commonly uppercase with commas, slashes, fractions, dimensions, and manufacturing abbreviations (e.g. `VALVE, SOLENOID VITON, 3/8`) — and may be long enough to span multiple display lines.
- The folder barcode identifies only the PN.
- Current revision is informational only.
- Revision changes do not create a new tracked PN unless ERP provides a different PN.
- The PN barcode is reused across all POs requesting the PN.

---

## 8.2 PurchaseOrder

Represents a PO received by the business.

Typical attributes (illustrative only):

- `id`
- `po_number`
- `received_date`
- `status`
- `erp_id`
- `created_at`
- `updated_at`

Rules:

- `po_number` must be treated as an arbitrary string.
- A PO contains one or more PO Demand records.
- A PO is complete when every PO Demand has been fully allocated.
- Completed POs move out of active views but remain permanently available in history.

---

## 8.3 PoDemand

Represents how many physical pieces of a PN are requested by one PO.

Typical attributes (illustrative only):

- `id`
- `purchase_order_id`
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

- A PN may have multiple active PO Demand records.
- Requested quantity represents physical pieces.
- PO Demand may be edited by Admin or Manager.
- Priority belongs to PO Demand.
- PO Demand does not own shop-floor Movement.
- PO Demand does not determine current PN location.
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
- `machine_assignment_mode`
- `worker_identification_mode`
- `created_at`
- `updated_at`

Rules:

- Area identity and barcode must remain stable.
- Area display name may change.
- An Area may contain zero, one, or multiple Machines.
- An Area may support one or multiple Operations.
- Stockroom is normally a terminal Area.
- Area configuration determines assignment behavior.

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
- `is_active`

Rules:

- Every Machine barcode must be unique.
- A Machine belongs to exactly one Area.
- Machine assignment identifies the current executor.
- Machine assignment is optional for Areas without Machines.
- Machine assignment behavior is controlled by Area configuration.

---

## 8.7 QuantityFlow

Represents an internal tracking concept for active PN quantity.

Typical attributes (illustrative only):

- `id`
- `part_number_id`
- `quantity`
- `status`
- `parent_flow_id`
- `created_at`
- `closed_at`

Rules:

- Quantity Flow is internal tracking structure, not a labeled physical batch.
- A Quantity Flow may split into child flows.
- Multiple Quantity Flows of the same PN may merge.
- Splitting and merging must preserve full history.
- Route assignment belongs to Quantity Flow.
- Current Position must be derived from Movement history.
- Quantity Flow must not be exposed to operators as unnecessary administrative complexity.

---

## 8.8 RouteTemplate

Represents a reusable production route definition.

Typical attributes (illustrative only):

- `id`
- `name`
- `description`
- `version`
- `is_active`

A Route Template contains ordered Route Steps.

A Route may visit the same Area more than once.

Changing a Route Template must never retroactively alter active assigned Routes.

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

Represents a Route snapshot assigned to one Quantity Flow.

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
- `reason`
- `reverses_movement_id`
- `device_event_id`
- `metadata`

Initial Movement types may include:

- `RECEIVED`
- `TRANSFERRED`
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE`
- `SPLIT`
- `MERGED`
- `STOCKED`
- `QUANTITY_ADJUSTED`
- `ROUTE_ADJUSTED`
- `ROUTE_DEVIATION_CONFIRMED`
- `REVERSED`

Rules:

- Movement records PN quantity.
- Movement is not tied to a specific PO Allocation.
- Movement must never be silently overwritten.
- Corrections preserve the original event.
- Current state must be reconstructable from Movement history.

---

## 8.12 PoAllocation

Represents the assignment of stocked PN quantity to PO Demand.

Typical attributes (illustrative only):

- `id`
- `part_number_id`
- `po_demand_id`
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
- Allocation must never exceed remaining PO Demand unless explicitly authorized as a correction.

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
- active Machine
- pending PN
- pending Operation
- pending quantity
- expiration time

ScanSession exists to reduce repetitive scanning and improves scanning efficiency.

It must never become the source of truth for production state.

---

# 10. Barcode Model

Barcode scanning is the primary interaction method.

Every barcode must identify its entity type deterministically.

Recommended logical formats:

```text
PF:PN:<stable-id>
PF:AREA:<stable-id>
PF:MACHINE:<stable-id>
PF:WORKER:<stable-id>
PF:ACTION:REWORK
PF:ACTION:MODIFY
```

Requirements:

- Barcode values must be unique.
- Barcode identity must not depend on mutable display names.
- Raw ERP PN text must not automatically be treated as a PartFlow barcode.
- Manual PN entry must remain available.
- Barcode parsing must be deterministic.
- Unknown barcodes must be rejected clearly.
- Inactive entities must not accept production updates.

The PN barcode identifies only the PN.

It does not encode:

- PO,
- PO Demand,
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

- multiple possible quantities at the source Area,
- multiple valid Operations in the Area,
- selecting between joining active production and creating Rework,
- unexpected Route destination,
- multiple pending scan contexts.

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

- quantity introduced into production,
- explicit quantity adjustments,
- stocked quantity,
- future scrap or rejected quantity if supported.

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
- does not modify PO Demand.

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

# 12. Processing Ownership Rules

## Area Without Machines

Example:

- Area: `External`
- Operation: `Plating`

When an operator scans PN quantity into the Area:

- the Area receives ownership,
- the quantity is considered actively processing,
- the configured Operation is recorded,
- Machine remains null.

If the Area supports multiple Operations, the applicable Operation must be resolved or confirmed.

---

## Area With One Machine

Behavior is controlled by Area configuration.

The Area may:

- automatically assign received quantity to the single Machine, or
- receive quantity into an Area queue until the Machine is explicitly selected.

The system must not infer business behavior solely from machine count.

---

## Area With Multiple Machines

When an operator scans PN quantity into the Area:

- the Area receives ownership,
- the quantity enters the Area queue,
- no Machine is assigned yet.

When the operator scans:

- PN barcode, and
- Machine barcode,

the quantity becomes assigned to that Machine.

Scan order must not matter.

The Machine remains the current executor until the quantity is:

- transferred,
- released,
- reversed,
- or reassigned through an explicit event.

---

# 13. Purchase Order Intake

POs may enter PartFlow through:

- manual entry,
- file import,
- future ERP synchronization.

For a newly received PO:

1. Create or locate the Purchase Order.
2. Create or update PO Demand for each PN.
3. Locate the reusable PN folder.
4. Create the PN master and barcode if the PN is new.
5. Add PO Demand without creating a separate tracked PN.
6. Save the business demand. Saving PO Demand never automatically creates production quantity.
7. Confirm or assign the initial Route when production is released.

Production release is a separate, explicit action. On production release:

1. Confirm the release quantity.
2. Confirm or assign the Route.
3. Confirm the configured starting Area and Operation.
4. Create the Quantity Flow.
5. Snapshot the Assigned Route.
6. Append an immutable `RECEIVED` Part Movement.
7. Derive or update the current-position projection atomically with the Movement.

New ERP production normally uses Request Type `NEW`.

The starting Area may be Material or another configured starting Area.

If the PN already has active quantity, the system must show the existing distribution and require explicit confirmation of intent. A new PO requesting an already active PN never automatically creates additional physical quantity and never automatically merges Quantity Flows.

Purchase Order and PO Demand represent business demand; creating or editing PO Demand does not define current production position. Production release explicitly introduces physical quantity. Part Movement remains PN + Quantity Flow + quantity activity: PO Demand does not own shop-floor Movement, and PO Allocation remains separate from both.

## PO Demand Removal

A PO Demand line may be removed from its Purchase Order only while no production quantity has been released for it:

- An unsaved draft line may be removed immediately.
- A saved PO Demand with no released production quantity may be removed only after explicit confirmation.
- Once any quantity for a PO Demand has been released to production, that PO Demand must not be deleted from Purchase Orders. Later adjustments go through the correction and production workflows (§16); removal is not a correction mechanism.

Removing a PO Demand must never delete the PartNumber master, any Quantity Flow, any Part Movement, release history, or other PO Demand records for the same PN.

---

# 14. Rework and Modify Intake

Rework and Modify represent why additional production work exists.

When Rework or Modify is introduced:

1. Scan or enter the PN.
2. Scan or select Request Type.
3. Confirm quantity.
4. Associate it with an applicable active PO when appropriate.
5. Otherwise create or select a temporary internal PO.
6. Assign a Route to the new Quantity Flow.
7. Receive the quantity directly into the required starting Area.

Suggested temporary PO format:

```text
TMP-20260721-1523-REWORK
TMP-20260721-1530-MODIFY
```

Temporary identifiers must be unique and auditable.

If the PN already has active quantity, the system must explicitly confirm whether the new quantity:

- joins an existing Quantity Flow,
- creates a separate Quantity Flow,
- represents Rework,
- represents Modify.

The system must never infer this from PN identity alone.

---

# 15. Core Scan Workflow

The normal workflow is keyboard- and scanner-first.

1. The Scan Station is bound to one Area.
2. The operator scans a barcode.
3. The system identifies barcode type.
4. The system validates entity state and scan context.
5. The system resolves PN, source quantity, Operation, and Machine context.
6. The system requests quantity only when necessary.
7. The system requests confirmation only when ambiguity or deviation exists.
8. The system records an immutable Movement.
9. The system derives the new production state.
10. The Area view and dashboards refresh immediately.
11. Barcode input regains focus.

For multi-machine Areas:

1. Scan PN.
2. Scan Machine.
3. Scan order may be reversed.
4. Once both valid inputs are available, assign the selected quantity.

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

---

## Scan Station Persistence

A Scan Station's identity and its binding to one Area are stable application and infrastructure configuration. A database table such as `scan_stations` is permitted; Scan Station configuration is not required to be a core domain aggregate.

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

Operators may undo only recent eligible actions when authorized.

Managers and Admins may perform broader corrections.

All corrections must remain auditable.

---

# 17. Production Routing

A Route describes the expected manufacturing path of a Quantity Flow.

Routes provide planning and tracking guidance.

They do not override actual Movement history.

---

## Route Template

A Route Template is a reusable sequence of Route Steps.

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

When production begins, a Route snapshot is assigned to a Quantity Flow.

Rules:

- Different Quantity Flows of the same PN may have different Routes.
- A split may inherit the parent Route or receive a modified Route.
- Route Template changes must not affect active assigned Routes.
- Authorized edits affect only the selected Quantity Flow.
- Actual Movement history remains the source of truth.

---

## Route Deviation

When quantity reaches an unexpected Area:

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
- the quantity becomes available for PO Allocation.

Production Movement and PO Allocation remain separate.

---

## Allocation Order

The system must suggest allocation using this exact priority:

1. Highest manager-defined PO Demand priority.
2. Earliest due date.

If both criteria are equal, implementation may use any stable deterministic tie-breaker such as PO Demand creation order or internal ID.

The tie-breaker is an implementation detail, not a business rule.

---

## Receiving Confirmation

The normal Stockroom workflow should be:

1. Scan PN.
2. Enter or confirm completed quantity.
3. Review suggested PO Allocation.
4. Confirm the allocation.

The suggestion must show:

- affected PO,
- requested quantity,
- previously allocated quantity,
- remaining shortage,
- proposed quantity.

Routine receiving should not require Manager approval.

Operators may review and adjust the suggested PO Allocation before confirmation.

Admin and Manager may adjust PO Allocation at any time.

Every change must remain auditable.

The total active allocation must equal the portion of stocked quantity being allocated and must never exceed available stocked quantity.

---

## PO Completion

A PO is complete when all of its PO Demand records are fully allocated.

When complete:

- the PO leaves active production views,
- the PO remains available in History,
- Movement history remains unchanged,
- later work must be represented by new PO Demand or new internal demand rather than reopening historical Movement.

---

# 19. Worker and Machine Sessions

Worker identification is configurable per Area.

Supported modes:

- no Worker identification,
- fixed Worker,
- Worker barcode session.

When Worker scanning is enabled:

- scanning a Worker barcode activates that Worker,
- subsequent scans use the active Worker,
- the session ends when another Worker signs in, the Worker signs out, or the session expires.

For Machine selection:

- Areas without Machines require no Machine session.
- Areas configured for direct single-Machine assignment may auto-select the Machine.
- Areas requiring Machine selection retain the active Machine until changed, cleared, or expired.

The active Worker and Machine must always be visible on the Scan Station.

Session state reduces repetitive scanning but must never replace persistent Movement history.

---

# 20. Roles and Permissions

PartFlow uses role-based authorization.

## Administrator

Administrator capabilities include:

- manage Departments,
- manage Areas,
- manage Operations,
- manage Machines,
- manage Workers,
- manage users and roles,
- manage barcode configuration,
- manage Route Templates,
- manage scan behavior,
- manage Worker and Machine session policies,
- manage correction permissions,
- edit PO Demand,
- edit PO Allocation,
- perform authorized historical corrections,
- configure system settings.

---

## Manager

Manager capabilities include:

- view all current and historical production data,
- create and edit POs,
- edit PO Demand,
- set PO Demand priority,
- reorder Hot items,
- assign and edit Routes,
- perform quantity corrections,
- edit PO Allocation,
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

The Scan Station is a fixed production interface assigned to one Area.

Requirements:

- one-screen normal workflow,
- no normal navigation,
- large focused barcode input,
- automatic input refocus,
- immediate validation feedback,
- visible Department, Area, and Operations,
- visible selected Machine,
- visible active Worker,
- visible pending scan context,
- visible last scanned PN,
- quantity entry only when required,
- recent scan history,
- current Area quantity,
- separate queued and Machine-assigned quantity,
- authorized Undo,
- manual entry fallback.

---

## Production Board

The Production Board is a read-only large-screen Department display.

Requirements:

- readable from a distance,
- automatic pagination,
- automatic page rotation,
- dynamic rows per page,
- priority sorting,
- due-date sorting,
- overdue highlighting,
- Area color display,
- distributed PN quantity display,
- time in current Area or Machine shown per distributed quantity,
- Part Number rendered on a single line.

Suggested columns:

| No. | Part Number | Areas and Quantities · Time | Job Numbers | Due Date | Total Days |
|---|---|---|---|---|---|

Days Left is displayed inside the Due Date column as a highlighted secondary line rather than as a separate column.

Example distribution with time in location:

```text
Cut (3 · 3h 40m), Lathe 1 (4 · 2h 05m), Lathe 2 (2 · 1h 10m), Mill (6 · 45m)
```

Time in location may be highlighted when it exceeds the expected duration of the active Route Step.

---

## Area Board

The Area Board provides a focused view of production currently owned by one Area.

It should show:

- PN,
- total quantity,
- Area queue,
- Operation,
- Machine distribution,
- associated active POs,
- Job Numbers,
- due dates,
- priority,
- time in Area,
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

## Tracking

Tracking is the primary management interface.

Managers must be able to search and filter by:

- PN,
- PO,
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
- active PO Demand,
- requested quantity by PO,
- allocated quantity by PO,
- remaining shortage by PO,
- current quantity by Area,
- current Machine assignments,
- Quantity Flows,
- assigned Routes,
- actual Movement history,
- time in each Area,
- stocked quantity,
- Allocation history,
- correction history.

Route visualization must distinguish:

- completed steps,
- active steps,
- queued steps,
- future steps,
- deviations.

It must not imply that the entire PN is at one Route Step.

---

## PO Intake

PO Intake is the management view for manual Purchase Order entry (§12). It is a light-theme management view.

The view must support the minimum confirmed workflow:

1. Create or locate a Purchase Order.
2. Add or update one or more PO Demand records.
3. Locate or create the PartNumber.
4. Create the PN barcode when the PN is new.
5. Enter: PO Number, received date, PN, Request Type (default `NEW`), requested quantity, due date, priority when applicable, external Job Numbers, and requester, reason, and notes when applicable.
6. Save business demand without automatically creating production quantity.
7. Provide a separate explicit `Release to production` action following the release steps in §12.

On production release the view must confirm release quantity, Route, and the configured starting Area and Operation, and show the resulting Quantity Flow, Route, Area, quantity, and `RECEIVED` Movement.

If the PN already has active quantity, the view must show the existing distribution and require explicit confirmation of intent; it must never automatically create additional physical quantity or merge Quantity Flows.

PO Intake must not grow into ERP-style customer, pricing, invoicing, shipping, purchasing, or accounting functionality.

---

## Priority Management

Priority belongs to PO Demand.

The Hot list is managed within the Department:

1. Show Hot PO Demand sorted by explicit priority rank.
2. Add PO Demand to the Hot list by searching and selecting, or by scanning the PN barcode.
3. If a PN has multiple active PO Demand records, each PO Demand is selected and ranked separately.
4. Add new Hot entries at the bottom by default.
5. Allow drag-and-drop reordering.
6. Allow removing an entry from the Hot list only after an explicit confirmation that identifies the PN and PO Demand; cancelling changes nothing. After confirmation the remaining ranks close the gap.
7. Apply Hot list changes immediately and record every change in the audit trail.
8. Provide Undo and Redo for recent Hot list changes instead of a separate save-or-cancel step.
9. Use the stored rank as the highest work and allocation priority.

Multiple POs requesting the same PN may have different priorities.

---

## Administration

Administration includes:

- Departments,
- Areas,
- Operations,
- Machines,
- Workers,
- users,
- roles,
- permissions,
- Route Templates,
- barcode configuration,
- scan behavior,
- Worker policies,
- Machine assignment policies,
- application settings.

Administrative workflows must remain separate from normal production scanning.

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
- Machine assignment behavior,
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
- PN and PO formats must never be assumed.
- Production history is owned by PartFlow.
- ERP changes must not erase local Movement history.
- Rework and Modify remain valid PartFlow concepts even when ERP does not model them.

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
4. Resolve ScanSession context.
5. Resolve source Quantity Flow.
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
- Which PO Demand was relevant, if any?
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

- active PO status,
- active PO Demand,
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
- PO Allocation,
- completed PO history,
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

- PO creation and edits,
- PO Demand creation and edits,
- priority changes,
- Route assignment,
- Route modification,
- quantity splits and merges,
- Area transfers,
- Machine assignments,
- Stockroom completion,
- PO Allocation,
- Allocation corrections,
- quantity adjustments,
- Undo,
- Worker Sessions,
- administrative configuration changes.

Historical production records must never disappear.

Database constraints should enforce, whenever practical:

- unique PN,
- unique PO Number where required by business rules,
- unique barcode values,
- unique Machine-to-Area relationships,
- non-negative quantities,
- valid Area/Machine relationships,
- allocation not exceeding available stock,
- idempotent scan-submission event identifiers (`device_event_id`).

---

# 29. Initial Scope

The initial release should support:

- Machine Shop,
- PN master records,
- reusable PN folder barcode,
- manual PO entry,
- file-based PO import,
- PO Demand,
- Rework,
- Modify,
- Areas,
- multiple Operations per Area,
- optional Machines,
- barcode scanning,
- quantity distribution,
- quantity splitting and merging,
- Quantity Flow routes,
- Stockroom completion,
- suggested PO Allocation,
- manual Allocation adjustment by Admin and Manager,
- optional Worker identification,
- Scan Station,
- PO Intake,
- Production Board,
- Area Board,
- Manager Summary,
- Tracking,
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
2. Whether scrap and rejected quantity are first-class Movement types in the initial release.
3. The exact expiration rules for Worker and Machine sessions.
4. Whether offline scan synchronization will be included in a later release.

Implementations must avoid assumptions that make these decisions difficult to change.

---

# 33. Guiding Principles

Every future feature must reinforce these principles:

- Track PN identity.
- Track physical quantity, not individual pieces.
- Keep PO Demand separate from production Movement.
- Keep PO Allocation separate from production Movement.
- Assign Routes to Quantity Flows.
- Derive current production state from immutable Movement history.
- Preserve quantity integrity.
- Prefer scanner-first workflows.
- Minimize operator interaction.
- Require confirmation when intent is ambiguous.
- Let production reality override outdated plans.
- Remain ERP-independent.
- Preserve complete auditability.
- Prefer the simplest design that accurately represents the shop floor.
