# PartFlow Project Profile

## 1. Purpose

PartFlow is an internal manufacturing tracking system designed to track Part Numbers as they move through physical processing areas.

The system must always be able to answer:

- Which Part Number is being processed.
- Which Areas currently hold quantities of that Part Number.
- How many physical parts are currently in each Area.
- Which Machine is currently processing a quantity, when applicable.
- Which Purchase Orders request that Part Number.
- How many pieces each Purchase Order requires.
- Which Areas the Part Number has passed through.
- Whether all requested quantities have been completed and stocked.
- How completed quantities are allocated to Purchase Orders.

PartFlow is not an ERP system. ERP integration may be added later, but the application must remain focused on lightweight shop-floor tracking.

---

## 2. Core Tracking Rule

The primary tracked object is the **Part Number**, abbreviated as **PN**.

A PN:

- Is unique in the ERP system.
- Has no guaranteed format.
- Must be treated as an arbitrary string.
- Has one reusable physical folder containing its drawings.
- Uses the same folder across all Purchase Orders requesting that PN.
- Has one unique barcode attached to the folder.
- May be requested by multiple active Purchase Orders at the same time.
- May have different quantities located in multiple Areas at the same time.
- Must remain permanently traceable after it is first recorded.

The system does not track each physical piece as an individual entity.

The system also does not treat `PO + PN` as the primary tracked object.

Instead:

- Movement is tracked at the PN level.
- Movement records contain physical quantities.
- PO demand is tracked separately.
- Completed quantities are allocated to Purchase Orders after stocking.

---

## 3. Fundamental Invariants

The following rules are mandatory:

1. A recorded PN must never become untraceable.
2. Movement history is immutable.
3. Corrections must be represented by explicit corrective events or authorized reversals, not by silently rewriting history.
4. Current PN location and status must be derived from movement history.
5. A PN may simultaneously have quantities in multiple Areas.
6. Every quantity movement must preserve quantity consistency.
7. Unknown or ambiguous scans must never update tracking data.
8. PO demand and shop-floor movement must remain separate concepts.
9. PO allocation may change without rewriting manufacturing movement history.
10. The MVP must work without ERP connectivity.

---

## 4. Canonical Vocabulary

Use the following terms consistently.

### Part Number

A reusable ERP-defined identifier for a type of manufactured part.

A Part Number is the primary tracked object.

Do not use `Part` when the intended meaning is specifically the ERP Part Number unless the surrounding context is unambiguous.

### Purchase Order

A customer or business order containing requested Part Numbers and required quantities.

Abbreviation: `PO`.

### PO Demand

The quantity of a PN requested by a specific PO.

A PN may have demand from multiple POs.

### Area

A physical place where parts are received, queued, processed, outsourced, or stocked.

Examples:

- Material
- Cut
- Lathe
- Mill
- Manual
- Deburr
- External
- Stockroom

`Area` is the preferred term because it represents a physical shop-floor location, not only a logical workflow stage.

### Operation

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

An Area usually has one primary Operation but may support multiple Operations.

Example:

- Area: `External`
- Operations: `Plating`, `Painting`, `Testing`

### Machine

A physical processing resource inside an Area.

A Machine is the current executor when a quantity is actively assigned to it.

A Machine also identifies where the quantity is physically located while being processed.

Machines are optional.

### Route

The ordered sequence of Areas or Operations through which a quantity of a PN is expected to travel.

A route is operational guidance, not a replacement for actual movement history.

### Part Movement

An immutable event recording the movement or assignment of a physical quantity of a PN.

### Current Position

The derived current Area and optional Machine for a quantity of a PN.

### PO Allocation

The assignment of completed stocked quantities to individual POs.

PO Allocation is independent from Part Movement and may be adjusted by authorized users.

### Priority

A manager-controlled ordering applied to PO demand or urgent work.

Higher priority overrides due date during allocation and work ordering.

### Hot Part

A PN or PO demand explicitly marked for expedited processing.

Use `Hot Part` only as a UI/business label. Use `Priority` for the underlying ordering rule.

---

## 5. Domain Model

## 5.1 PartNumber

Represents the ERP-defined PN master record.

Suggested attributes:

- `id`: internal immutable identifier
- `part_number`: ERP PN string, unique
- `barcode_value`: unique system barcode value
- `image_url`: optional
- `description`: optional
- `erp_id`: optional external ERP identifier
- `created_at`
- `updated_at`

Rules:

- `part_number` must be unique.
- The PN string may not be changed casually because it is an external business identifier.
- The barcode must identify the record as a PN barcode.
- The folder barcode is reused across all POs requesting the PN.

---

## 5.2 PurchaseOrder

Represents a PO received by the business.

Suggested attributes:

- `id`: internal immutable identifier
- `po_number`: external PO string
- `received_date`
- `status`
- `erp_id`: optional
- `created_at`
- `updated_at`

A PO contains one or more PO Demand records.

---

## 5.3 PoDemand

Represents how many physical pieces of a PN are required by a PO.

Suggested attributes:

- `id`
- `purchase_order_id`
- `part_number_id`
- `request_type`
- `requested_quantity`
- `allocated_quantity`
- `due_date`: optional
- `priority_rank`: optional
- `job_numbers`
- `created_at`
- `updated_at`

Possible `request_type` values:

- `NEW`
- `REWORK`
- `MODIFY`

Rules:

- A PN may have multiple active PO Demand records.
- Requested quantity represents physical pieces.
- PO demand may be edited by Admin or Manager.
- Allocation may be adjusted at any time by Admin or Manager.
- PO demand does not determine the physical location of the PN.

---

## 5.4 Area

Represents a physical place in the workflow.

Suggested attributes:

- `id`: internal immutable identifier
- `barcode_value`: immutable unique barcode identifier
- `name`
- `department_id`
- `description`: optional
- `color`: optional
- `icon_url`: optional
- `is_terminal`
- `is_active`
- `created_at`
- `updated_at`

Rules:

- Area name may change.
- Area ID and barcode identity must remain immutable.
- An Area may have zero, one, or multiple Machines.
- An Area may support one or multiple Operations.
- Stockroom is normally a terminal Area.

---

## 5.5 Operation

Represents a type of work performed in an Area.

Suggested attributes:

- `id`
- `area_id`
- `name`
- `description`: optional
- `is_active`

Examples:

- `Cut` Area → `Cutting`
- `Lathe` Area → `Turning`
- `External` Area → `Plating`, `Painting`, `Testing`

---

## 5.6 Machine

Represents a physical machine or processing station.

Suggested attributes:

- `id`
- `area_id`
- `name`
- `barcode_value`
- `is_active`
- `description`: optional

Rules:

- Every Machine barcode must be unique.
- A Machine always belongs to one Area.
- A Machine assignment identifies the current executor.
- Machine assignment is not required for Areas without Machines.

---

## 5.7 RouteTemplate

Represents a reusable route definition.

Suggested attributes:

- `id`
- `name`
- `description`
- `version`
- `is_active`

A Route Template contains ordered Route Steps.

Changing a Route Template must not retroactively modify routes already assigned to active work.

---

## 5.8 AssignedRoute

Represents the route assigned to active work for a PN.

Rules:

- It is copied from a Route Template when assigned.
- It becomes independent after assignment.
- It may be edited by authorized users.
- Actual movement history remains the source of truth.
- If a PN is scanned into an unexpected Area, the system must require confirmation before accepting the deviation.
- Accepted deviations must update the assigned route or record the deviation explicitly.

Because quantities of the same PN may be split across multiple Areas, route progress must support quantity-level progression rather than assuming the entire PN moves as one unit.

---

## 5.9 PartMovement

Represents an immutable quantity event for a PN.

Suggested attributes:

- `id`
- `part_number_id`
- `quantity`
- `movement_type`
- `from_area_id`: optional
- `to_area_id`: optional
- `machine_id`: optional
- `operation_id`: optional
- `scan_session_id`: optional
- `performed_by_worker_id`: optional
- `occurred_at`
- `reason`: optional
- `reverses_movement_id`: optional
- `metadata`: optional

Possible movement types:

- `RECEIVED`
- `MOVED`
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE`
- `STOCKED`
- `QUANTITY_ADJUSTED`
- `REVERSED`
- `ROUTE_DEVIATION_CONFIRMED`

Rules:

- A Part Movement records total PN quantity only.
- It is not tied to a specific PO allocation.
- It must never be silently overwritten.
- Corrections must preserve the original event.
- Current location must be derived from the event stream.

---

## 5.10 PoAllocation

Represents the assignment of stocked quantities to PO Demand records.

Suggested attributes:

- `id`
- `part_number_id`
- `po_demand_id`
- `quantity`
- `allocated_at`
- `allocated_by_worker_id`
- `allocation_reason`
- `is_manual_override`

Rules:

- Allocation occurs from completed stocked quantity.
- Allocation does not change movement history.
- Admin or Manager may adjust allocation at any time.
- Allocation priority is:
    1. Explicit manager-defined priority
    2. Earliest due date
    3. Largest remaining shortage or configured quantity rule
- Manual changes must be auditable.

The exact third-level quantity tie-breaker should remain explicit and deterministic in implementation.

---

## 6. Barcode Design

PNs currently have no barcode format, so PartFlow may define one.

Recommended logical barcode formats:

```text
PF:PN:<internal-id-or-encoded-key>
PF:AREA:<area-id>
PF:MACHINE:<machine-id>
PF:ACTION:REWORK
PF:ACTION:MODIFY
```

Requirements:

- Every barcode must identify its entity type.
- A scanner input must be validated before any write occurs.
- Raw ERP PN text must not be assumed to be a PartFlow barcode.
- Barcode parsing must be deterministic.
- Barcode values must be unique.
- Area and Machine IDs must remain valid even if display names change.
- Manual PN entry must remain available as a fallback.

The barcode payload should use stable internal identifiers rather than mutable display names.

---

## 7. Quantity Model

Quantity always represents physical parts.

A PN may be split across Areas.

Example:

```text
PN ABC
- Cut: 10 pieces
- Lathe: 6 pieces
- Mill: 4 pieces
```

The sum of active quantities must remain reconcilable with:

- quantities introduced into the workflow,
- quantity adjustments,
- stocked quantities,
- scrapped or removed quantities if those states are later supported.

The system must never assume that one PN has only one current location.

---

## 8. Processing Ownership Rules

## 8.1 Area Without Machines

Example:

- Area: `External`
- Operation: `Plating`

When an operator scans the PN at that Area:

- The Area receives ownership of the scanned quantity.
- The quantity is considered actively being processed in that Area.
- No Machine assignment is required.

---

## 8.2 Area With One Machine

The system may treat receipt as direct assignment when the Area configuration explicitly allows it.

The Area owns the quantity, and the configured Machine becomes the executor.

This behavior must be configurable rather than inferred only from machine count.

---

## 8.3 Area With Multiple Machines

When an operator scans the PN at the Area:

- The Area receives ownership of the quantity.
- The quantity enters the Area queue.
- The quantity is not yet assigned to a Machine.

When a machine operator scans both:

- the PN barcode, and
- the Machine barcode,

the quantity becomes assigned to that Machine.

Scan order must not matter.

The assignment remains active until the quantity is received by another Area, released, reversed, or otherwise explicitly updated.

---

## 9. Purchase Order Intake

POs may be entered through:

- manual entry,
- file import,
- future ERP synchronization.

For a newly received PO:

1. Create or locate the Purchase Order.
2. Create or update PO Demand for each PN.
3. Locate the existing PN folder.
4. If the PN does not exist in PartFlow, create its master record and barcode.
5. If the PN already has active work, add the new PO Demand without creating a separate tracked PN.
6. Assign or confirm the route.
7. Move the required quantity into the first Area when work begins.

New PO demand normally starts as `NEW`.

The initial Area may be `Material` or another configured starting Area.

---

## 10. Rework and Modify Intake

Rework and Modify are request types, not separate tracked objects.

When a PN is introduced as Rework or Modify:

1. Scan or enter the PN.
2. Scan or select the request type.
3. Confirm quantity.
4. Associate the demand with an active PO when appropriate.
5. Otherwise create or select a temporary internal PO.
6. Assign the required route.
7. Receive the quantity directly into the first required Area.

Suggested temporary PO number format:

```text
TMP-20260720-1523-REWORK
TMP-20260720-1530-MODIFY
```

Temporary PO generation must avoid collisions and remain auditable.

If the PN already has active quantities, the user must explicitly confirm whether the new quantity joins the active work.

No uncertain input may update existing quantities automatically.

---

## 11. Scan Workflow

The normal workflow is keyboard- and scanner-first.

1. The Scan View is bound to one Area.
2. The operator scans a PN barcode.
3. The system identifies the barcode type.
4. The system validates the PN and expected context.
5. The system determines whether quantity confirmation is required.
6. The system records an immutable movement event.
7. The system derives the new current position.
8. The Area list and dashboards refresh immediately.

For multi-machine Areas:

1. Scan PN barcode.
2. Scan Machine barcode.
3. The order may be reversed.
4. Once both valid inputs are present, assign the quantity to the Machine.

The system must clearly reject:

- unknown barcodes,
- inactive Areas or Machines,
- invalid Area/Machine combinations,
- impossible quantities,
- ambiguous PN input,
- unauthorized reversals,
- unexpected route changes without confirmation.

---

## 12. Undo and Correction

Operators may undo a recent mistaken scan when authorized.

Undo must not delete history.

Instead, the system must:

- create a reversal event,
- reference the original movement,
- restore the derived quantity position,
- record the user and timestamp,
- require a reason when configured.

Manager and Admin may perform broader corrections.

All corrections must remain auditable.

---

## 13. Stockroom and Completion

Stockroom is the normal terminal Area.

When a quantity is scanned into Stockroom:

- A `STOCKED` movement is recorded.
- The quantity is considered manufacturing-complete.
- The quantity becomes available for PO Allocation.

Stocked quantity is allocated to PO Demand using:

1. Manager-defined priority
2. Earliest due date
3. Remaining shortage quantity according to a deterministic configured rule

Admin and Manager may manually adjust allocation at any time.

A PO is complete when all of its PO Demand quantities have been fully allocated.

When every demand line is complete:

- the PO moves out of active production views,
- the PO remains available in history and reporting,
- all PN movement history remains accessible.

---

## 14. Roles and Permissions

## 14.1 Admin

Admin has full system access.

Capabilities include:

- manage Areas,
- manage Operations,
- manage Machines,
- manage users,
- manage permissions,
- manage barcode configuration,
- manage routes,
- manage notification and confirmation rules,
- edit PO demand,
- edit PO allocation,
- perform authorized corrections,
- configure views and system settings.

---

## 14.2 Manager

Manager may:

- view all active and historical tracking data,
- create and edit POs,
- edit PO Demand,
- set priority,
- reorder Hot Parts,
- assign and edit routes,
- edit PO Allocation,
- perform authorized movement corrections,
- export and print reports.

---

## 14.3 Operator

Operator may:

- scan PN barcodes,
- scan Machine barcodes,
- receive quantities into an Area,
- assign quantities to Machines,
- confirm quantities when required,
- undo recent scans when authorized.

Operator must not directly rewrite historical data.

---

## 15. User Interfaces

## 15.1 Scan View

Purpose:

- Receive and assign PN quantities at one Area.

Primary device:

- Tablet or fixed workstation with barcode scanner.

Requirements:

- Single fixed page.
- Bound to one Area.
- No normal route navigation.
- Large focused scan input.
- Keyboard- and scanner-first.
- Immediate barcode type detection.
- Immediate validation feedback.
- Show recently scanned PN details.
- Show quantities currently owned by the Area.
- Show queued and machine-assigned quantities separately when relevant.
- Allow authorized undo.
- Support manual entry fallback.
- Support configurable quantity confirmation.

Suggested content:

- Department name
- Area name
- Area description
- Scan input
- Current scan state
- PN details
- Quantity entry or confirmation
- Area queue
- Active Machine assignments
- Recent activity

---

## 15.2 Production Board

Purpose:

- Large-screen department-wide status display.

Requirements:

- Read-only.
- Fixed display.
- Automatically paginated.
- Automatically rotates pages.
- Dynamically determines page size from viewport and font size.
- Sorts by:
    1. Hot/Priority rank
    2. Due date
- Highlights overdue items.
- Uses Area colors.
- Shows PN quantities across Areas.

Suggested columns:

| No. | Part Number | Areas and Quantities | Job Numbers | Due Date | Days Left | Total Days |
|---|---|---|---|---|---|---|

A PN may display multiple Area quantities:

```text
Cut (3), Lathe (6), Mill (2)
```

---

## 15.3 Manager Summary

Purpose:

- Operational overview grouped by Area.

Each Area column should show:

- Area name
- Area description
- total physical quantity
- search
- sort controls
- PN list
- queued quantities
- machine assignments where applicable

Default sorting:

1. Priority
2. Due date

The layout should support horizontal scrolling when all Areas do not fit.

---

## 15.4 Management View

Purpose:

- Full searchable and editable operational table.

Requirements:

- Search per column.
- Sort per column.
- Show/hide columns.
- Long continuous list.
- Export to PDF or spreadsheet.
- Print support.
- PN detail view.
- Full movement history.
- Current quantity distribution.
- Assigned route and actual route.
- PO demand and allocation.
- Authorized edits and corrections.

---

## 15.5 PN Detail View

The PN detail view must show:

- PN master information
- barcode
- image
- active PO Demand
- requested quantity per PO
- allocated quantity per PO
- current quantity per Area
- current Machine assignments
- assigned route
- actual movement history
- time spent in each Area
- stocked quantity
- allocation history
- correction history

The route visualization should distinguish:

- completed steps,
- active steps,
- queued steps,
- future steps,
- route deviations.

Because quantities may be split, the visualization must not imply that the entire PN is always at one route step.

---

## 15.6 Priority Management

When a Manager marks work as Hot:

1. Show all Hot items in the Department.
2. Sort by explicit priority rank.
3. Add new Hot items at the bottom by default.
4. Allow drag-and-drop reordering.
5. Save or cancel changes.
6. Use the stored rank as the highest allocation and display priority.

The system should define whether priority applies to the entire PN or to an individual PO Demand. The preferred model is PO Demand priority because different POs requesting the same PN may have different urgency.

---

## 15.7 Settings and Administration

Manager settings may include:

- display columns,
- page rotation interval,
- font size,
- confirmation preferences,
- default sorting.

Admin settings may additionally include:

- Areas,
- Operations,
- Machines,
- routes,
- users,
- permissions,
- barcode formats,
- quantity confirmation rules,
- scan behavior,
- direct assignment rules,
- correction permissions.

---

## 16. Department Scope

The initial deployment targets the Machine Shop Department.

The design must support future Departments such as:

- Purchasing
- Assembly
- Production
- Outsourcing
- Stockroom

The application must not hard-code Machine Shop assumptions into core domain logic.

Department-specific configuration may define:

- Areas,
- Operations,
- Machines,
- routes,
- scan behavior,
- completion Area,
- display settings.

---

## 17. ERP Boundary

ERP integration must remain isolated.

Rules:

- MVP must work without ERP.
- Store ERP IDs separately from internal IDs.
- Do not use ERP response models inside domain logic.
- Use explicit synchronization.
- Make imports idempotent when practical.
- Treat ERP PN and PO values as arbitrary strings.
- Never assume fixed formatting.
- Preserve local tracking history even if ERP data changes.

---

## 18. Application Architecture

Preferred stack:

- React or Next.js
- FastAPI
- PostgreSQL

Layering:

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

- Keep API routes thin.
- Put scan orchestration in the Application layer.
- Keep business rules in Domain/Application.
- Keep Domain independent from frameworks.
- Use database transactions for scan updates.
- Use database constraints for identity and quantity integrity.
- Preserve immutable movement history.
- Avoid global mutable state.
- Keep ERP integration behind an explicit boundary.

---

## 19. Transaction Requirements

A scan update must be atomic.

A successful scan transaction may include:

1. validate barcode,
2. validate permissions,
3. validate quantity,
4. validate route,
5. record movement,
6. update or rebuild derived current state,
7. update dashboard projections,
8. commit.

If any required step fails, the transaction must roll back.

The system must never partially record movement.

---

## 20. Logging

Logs must answer:

- What happened?
- Which PN?
- Which quantity?
- Which PO demand was relevant, if any?
- Which Area?
- Which Machine?
- Which user?
- Why did it fail?
- Was the action reversed or corrected?

Avoid duplicated or noisy logging.

Do not expose raw internal exceptions to operators.

---

## 21. Reporting

The system should support:

- active PO status,
- active PN status,
- quantity by Area,
- quantity by Machine,
- overdue demand,
- priority demand,
- movement history,
- time in Area,
- stocked quantity,
- PO allocation,
- completed PO history,
- route deviation history,
- correction history.

Reports must distinguish:

- manufacturing movement,
- PO demand,
- PO allocation.

---

## 22. Explicit Non-Goals

PartFlow is not intended to become:

- a full ERP,
- an accounting system,
- a per-piece serial tracking system,
- a CNC controller,
- a machine telemetry platform,
- an inventory valuation system,
- an automatic ERP replacement.

Do not add these responsibilities without an explicit project decision.

---

## 23. Open Decisions

The following decisions should be finalized during implementation:

1. Whether priority is stored only on PO Demand or may also exist as a PN-wide override.
2. The deterministic third-level PO allocation tie-breaker after priority and due date.
3. Whether single-machine Areas automatically assign received quantities or require an explicit Machine scan.
4. How route assignment is represented when different quantities of the same PN require different routes.
5. Whether shortage, scrap, and rejected quantities are first-class movement types in the MVP.
6. Whether a stocked quantity may be unstocked through a controlled reversal.
7. Whether temporary internal POs are visible in all reports or grouped separately.

Until explicitly decided, implementations must avoid assumptions that make these choices difficult to change.
