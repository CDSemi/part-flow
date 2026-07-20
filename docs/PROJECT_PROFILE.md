# PartFlow Project Profile v3

> **Status:** Living Document

---

# 1. Project Overview

## Purpose

PartFlow is an internal shop-floor tracking system designed to monitor CNC parts as they move through the manufacturing process.

Its primary objective is to provide real-time visibility into:

- where every part is currently located,
- how much quantity is being processed,
- which Operation is being performed,
- which Machine is working on it when a specific Machine is tracked,
- what work remains,
- and when it is expected to be completed.

PartFlow is intentionally focused on production tracking rather than business management. It is **not** an ERP, MES, inventory system, or production scheduling system, although it may integrate with those systems in the future.

The initial deployment targets the **Machine Shop** department. The architecture should remain flexible enough to support additional departments such as Purchasing, Assembly, Production, Stockroom, or Quality Control without changing the core domain model.

---

# 2. Design Goals

PartFlow is designed around real shop-floor operations rather than idealized manufacturing workflows.

The system should always prioritize:

1. Operational simplicity.
2. Tracking accuracy.
3. Fast barcode-driven workflows.
4. Complete movement history.
5. Minimal operator interaction.
6. Future scalability.

Whenever trade-offs exist, practical day-to-day usability should take precedence over theoretical perfection.

The application should help operators perform their work with as few interactions as possible while still preserving reliable production data.

---

# 3. Design Principles

## Scanner First

Barcode scanning is the primary interaction method.

Keyboard wedge scanners should work without custom drivers.

Mouse interaction should be minimized.

Touch interaction should be optimized for tablets.

Manual entry remains available as a fallback.

---

## Production-Oriented

The application tracks production activities rather than administrative activities.

Business workflows should follow how parts actually move through the shop instead of forcing operators to adapt to the software.

---

## Quantity-Based Tracking

PartFlow tracks **quantities**, not individual physical pieces.

Individual parts are not labeled.

Instead, one reusable barcode attached to the drawing folder represents a Part Number.

Quantities may be split across multiple production Areas while continuing to share the same Part Number.

---

## History Is Immutable

Every production movement must be recorded.

Corrections create additional history entries rather than modifying or deleting previous records.

The application always preserves a complete audit trail.

---

## ERP Independent

The application must function without ERP integration.

ERP data may be imported manually today and synchronized automatically in the future.

Business rules inside PartFlow must never depend on ERP-specific concepts or APIs.

---

## Practical Before Perfect

Real manufacturing environments contain exceptions.

The application should assist operators in handling unexpected situations rather than rejecting every deviation.

Whenever ambiguity exists, the system should require confirmation instead of making assumptions.

---

# 4. Operational Context

Each reusable part design is identified by a **Part Number**.

A reusable drawing folder is maintained for every Part Number.

The drawing folder belongs to the Part Number rather than to any specific Purchase Order.

The same folder may therefore be reused across:

- multiple Purchase Orders,
- multiple production runs,
- and future drawing revisions.

The folder barcode always identifies the Part Number.

The company intentionally does **not** label every individual physical part because:

- parts begin as raw material,
- labels would not survive machining,
- attaching labels to every piece is operationally impractical,
- and staffing is insufficient for individual piece tracking.

PartFlow therefore tracks **production quantities** instead of individual physical pieces.

---

## Shared Drawing Folder

The drawing folder is primarily required during machine setup.

After setup has been completed, production quantities may continue moving independently while the drawing folder is temporarily used elsewhere.

The physical location of the folder is therefore independent from the production quantities being tracked.

The application should never assume that the drawing folder and all production quantities are physically together after production has begun.

---

# 5. Terminology

The following terms define the vocabulary used throughout the project.

These names should be used consistently in documentation, source code, APIs, database design, and user interfaces.

---

## Purchase Order (PO)

A Purchase Order represents manufacturing work received from the ERP system.

A Purchase Order contains one or more Production Requests.

A PO Number:

- originates from the ERP,
- has no fixed format,
- and should always be treated as an arbitrary string.

A Purchase Order is considered complete only after every requested quantity has been received into Stockroom.

Completed Purchase Orders move to History and never require reopening. Subsequent work must be represented by new Production Requests.

Additional production must always be represented by a new Purchase Order or a new internal Production Request.

---

## Part

A Part represents a reusable product definition identified by its Part Number.

A Part Number:

- is unique,
- originates from the ERP system,
- has no predefined format,
- remains unchanged even when drawing revisions change,
- and is encoded in the reusable drawing folder barcode.

A Part may simultaneously appear in multiple active Purchase Orders.

Suggested information includes:

- Part Number
- Name
- Description
- Image
- Current Drawing Revision
- ERP Reference
- Active Status

The Part itself is a reusable master definition.

It does not represent production demand.

Current Drawing Revision is informational only and does not participate in production tracking.

---

## Production Request

A Production Request represents production demand for a Part.

Production Requests may originate from:

- an ERP Purchase Order,
- an internal Production Request,
- a Rework Request,
- or a Modification Request.

Although operators only scan the Part barcode, Production Requests allow the system to distinguish multiple simultaneous production demands for the same Part Number.

A Production Request may contain:

- Request Number,
- requested quantity,
- requester,
- reason,
- due date,
- priority,
- request type,
- external Job Numbers,
- completion status.

Production Requests are business objects.

Production Requests are never encoded into the reusable folder barcode.

Each Production Request has a PartFlow-owned Request Number. An external Job Number is optional reference data from an external system and must remain a separate field. PartFlow must never use the terms Request Number and External Job Number interchangeably.

---

## Request Type

Request Type describes why production work exists.

Initial values include:

- Production
- Rework
- Modification

ERP Purchase Orders always create Production Requests with Request Type **Production**.

Rework and Modification Request Types are internal manufacturing concepts owned entirely by PartFlow.

They are never required to exist inside the ERP system.

---

## Department

A Department represents a major organizational unit.

Examples include:

- Machine Shop
- Purchasing
- Assembly
- Production
- Stockroom
- Quality Control

The initial release targets the Machine Shop department only.

---

## Area

An Area represents a physical production location where work is received, processed, or transferred.

Examples:

- Material
- Cut
- Lathe
- Mill
- Manual
- Deburr
- External
- Stockroom

An Area has a stable identity throughout the system.

Display properties such as name, color, description, and icon may change without affecting production history.

An Area defines where production work occurs.

Each Area has one Operation that describes the work performed there.

Area configuration includes its primary Operation and whether Machine tracking is enabled.

An Area may contain zero or more Machines. Machine tracking is optional and is used only when PartFlow needs to identify a specific production resource.

Examples:

- Area: Cut, Operation: Cutting, Machine: Saw 1
- Area: Lathe, Operation: Turning, Machine: Lathe 2
- Area: Deburr, Operation: Deburring, Machine: none
- Area: External, Operation: Plating, Machine: none

---


## Operation

An Operation describes the type of production work performed in an Area.

Examples include:

- Cutting
- Turning
- Milling
- Deburring
- Plating
- Receiving

Each Area performs exactly one primary Operation.

An Operation describes the type of production work performed within an Area and may contain:

- Code
- Name
- Description
- Default Expected Duration
- External Status
- Machine Requirement

Operation does not require a barcode because it is resolved from the active Area.

---

## Machine

A Machine represents an individual production resource inside an Area. Machine is optional.

Example:

Area: Lathe

Machines:

- Lathe 1
- Lathe 2
- Lathe 3
- Lathe 4

Each Machine has its own barcode.

Areas containing no Machines record production against the Area and its Operation while Machine remains null.

Areas containing only one Machine may automatically select it.

Areas containing multiple Machines require an active Machine selection before production scanning begins.

---

## Worker

A Worker represents the operator performing production activities.

Worker identification is configurable per Area.

Depending on the Area configuration:

- Worker identification may be disabled.
- A default Worker may be assigned.
- Operators may scan their Worker barcode before scanning Parts.

Worker identity exists for accountability and reporting.

Production logic should never depend on Worker identity.

# 6. Core Domain

The following concepts form the core business model of PartFlow.

These concepts describe **what** the system tracks rather than **how** it is implemented.

---

## Part-Centric Tracking

The Part Number is the central identity used throughout production.

The reusable drawing folder barcode always represents a Part Number.

A Part may simultaneously have:

- multiple active Production Requests,
- quantities in multiple production Areas,
- multiple active Purchase Orders,
- and multiple external Job Numbers.

The application should always treat the Part as the stable product identity while Production Requests represent production demand and Movements represent production activity.

---

## Production Requests

Production begins with one or more Production Requests.

Production Requests never split. Only production quantities split.

A Production Request represents **production demand**, not production state.

Examples include:

- ERP Purchase Order
- Internal Production Request
- Rework Request
- Modification Request

Multiple Production Requests may exist simultaneously for the same Part Number.

Production Requests remain independent throughout their lifecycle even when production quantities are processed together.

---

## Queued Production Requests

When a new Production Request is created while production of the same Part Number is already active, the Production Request normally enters a queued state.

Example:

```text
PF-BRACKET-001

Running
--------
PO-1001
Qty: 10

Queued
------
PO-1008
Qty: 5

Queued
------
Rework
Qty: 2
```

Queued Production Requests are production demand waiting to be released.

Managers may later decide to:

- release production,
- combine production,
- delay production,
- reprioritize Production Requests,
- or cancel Production Requests.

The application must never automatically merge Production Requests simply because they reference the same Part Number.

---

## Production State

At any moment, the application should be able to answer:

- How much quantity is currently active?
- Which Areas contain those quantities?
- Which Machines are processing them?
- Which Production Requests still require completion?
- Which Purchase Orders are affected?
- Which quantities have already been completed?

Current production state is derived from recorded production Movements rather than manually maintained status fields whenever practical.

---

# 7. Barcode Model

Barcode scanning is the primary interaction method throughout PartFlow.

Every barcode represents a business object.

Operators should never need to manually select a scan mode before scanning.

The application determines the barcode type automatically.

The exact barcode format is implementation-specific and may evolve over time, but the business meaning of each barcode must remain stable.

---

## Supported Barcode Types

The initial system supports:

- Part
- Area
- Production Resource
- Worker
- Request Type
- Administrative Commands (future)

Additional barcode types may be introduced without changing the production workflow.

### Production Resource Barcode

A Production Resource barcode identifies the production resource currently used by the Area.

When an Area tracks Machines, the barcode identifies a Machine.

Otherwise, the barcode identifies the Area itself and implicitly selects its primary Operation.

Operators use the same scanning workflow regardless of whether the underlying production resource is a Machine or an Operation.

---

## Part Barcode

The Part barcode represents a reusable Part Number.

It is attached to the drawing folder rather than the physical parts.

Only one reusable barcode should normally exist for a Part Number.

The barcode identifies only the Part.

It does **not** identify:

- Purchase Order
- Production Request
- Quantity
- Request Number
- External Job Number

These contexts are resolved by the application.

---

## Area Barcode

Each production Area has its own barcode.

Area barcodes are primarily used during setup or administration.

Normal production scanning should not require repeatedly scanning the Area because every Scan Station is permanently assigned to a specific Area.

---

## Machine Barcode

Each Machine has its own barcode.

When an Area contains multiple Machines, scanning a Machine selects the active production resource.

Subsequent production scans automatically use that Machine until:

- another Machine is selected,
- the selection is cleared,
- or the Machine session expires.

Areas containing only one Machine may automatically select it.

---

## Worker Barcode

Worker identification is configurable.

Depending on Area configuration:

- Worker scanning may be disabled.
- A default Worker may be assigned.
- Operators may be required to identify themselves before scanning Parts.

Worker identity exists only for accountability and reporting.

Production logic must never depend on Worker identity.

---

## Request Type Barcode

Request Type barcodes allow operators to create internal Production Requests without navigating through menus.

Initial Request Types include:

- Production
- Rework
- Modification

The order of scanning is intentionally flexible.

Example:

```text
Part → Rework
```

and

```text
Rework → Part
```

produce the same result.

---

## Barcode Resolution

Scanning a Part barcode does not always uniquely identify production intent.

When only one valid production context exists, the application may continue automatically.

When multiple valid contexts exist, the application must never guess.

Instead, it should present the relevant choices, for example:

- Continue an existing Production Request
- Continue an existing production quantity
- Create a Production Request
- Create a Rework Request
- Create a Modification Request

The available choices should be filtered by the current production context whenever possible.

Whenever ambiguity remains, explicit confirmation is required before modifying production data.

---

# 8. Quantity Distribution

PartFlow tracks production by **quantity**, not by individual physical pieces.

The application does not assign identities to individual parts.

At any moment, quantities may be distributed across multiple Areas and Machines.

Example:

```text
PF-BRACKET-001

Material      5
Cut           4
Lathe 1       3
Lathe 2       2
Mill          6
```

The sum of all active quantities always represents the total quantity currently in production.

---

## Quantity Splitting

Production quantities may be divided whenever required by manufacturing operations.

Example:

```text
Material

10
```

↓

```text
Cut      4
Lathe    6
```

Splitting changes only the current production distribution.

It must never change the original Production Request.

---

## Quantity Merging

Separate production quantities belonging to the same Part Number may later converge into the same Area or Machine.

Example:

```text
Lathe 1    2

Lathe 2    4
```

↓

```text
Mill       6
```

Movement history must continue to preserve how those quantities arrived.

---

## Quantity Integrity

The application must preserve quantity integrity at all times.

Production operations must never:

- create quantity,
- destroy quantity,
- duplicate quantity,
- produce negative quantity,
- move more than the available quantity,
- complete more quantity than requested without an explicit adjustment.

Any intentional quantity correction must be explicitly recorded.

History must never be silently rewritten.

---

# 9. Completion Allocation

Production quantities and Production Requests intentionally remain independent during manufacturing.

The application separately tracks:

- where production quantities currently are,
- and which Production Requests still require completion.

These two views become connected only when completed quantities enter Stockroom.

---

## Automatic Suggestion

When completed quantities are received into Stockroom, the application should suggest how those quantities should be allocated to outstanding Production Requests.

Allocation policies are configurable.

Possible strategies include:

- Earliest Due Date
- Highest Priority
- FIFO
- Manual

The suggested allocation is only an initial proposal.

---

## Operator Confirmation

The receiving operator may accept or modify the suggested allocation before confirming completion.

Example:

```text
Completed Quantity

6
```

Suggested allocation:

```text
PO-1001    4

PO-1008    2
```

The operator may adjust the allocation to match the actual production situation.

The total allocated quantity must always equal the completed quantity.

Normal receiving operations should not require Manager involvement.

---

## Manager Responsibilities

Managers supervise exceptional situations rather than routine receiving.

They may:

- review completed allocations,
- correct allocation mistakes,
- resolve production disputes,
- modify historical allocations when authorized.

Production should continue efficiently without waiting for Manager approval.

---

# 10. Core Production Workflow

The workflow intentionally mirrors how production actually operates on the shop floor.

The application should never require operators to perform administrative work that does not exist in the physical manufacturing process.

---

## ERP Purchase Order

1. Import or manually enter the Purchase Order.
2. Create Production Requests.
3. Place Production Requests into the production queue.
4. Review routing if necessary.
5. Release production when appropriate.

---

## Internal Production Request

When a scanned Part Number does not belong to an active Production Request, the operator may create an internal Production Request.

Supported Request Types:

- Production
- Rework
- Modification

Required information:

- Quantity
- Requester
- Reason

Optional information:

- Due Date
- Notes

The application automatically records:

- creation time,
- current Area,
- current Machine,
- Worker (when available).

---

## Receiving Production

When production arrives at an Area:

1. Select the active Machine if required.
2. Scan the Part.
3. Enter quantity when required.
4. Review warnings if any.
5. Record the Movement.
6. Refresh production state.

The normal workflow should complete without unnecessary dialogs or button presses.

---

## Completing Production

When completed quantities reach Stockroom:

1. Scan the Part.
2. Enter completed quantity if necessary.
3. Review the suggested allocation.
4. Modify the allocation if required.
5. Confirm.
6. Record the completion Movement.
7. Update Production Request completion status.
8. Check affected Purchase Orders.
9. Automatically move completed Purchase Orders to History.

---

## Undo

Undo exists to correct scanning mistakes.

Undo is intended only for correcting scans related to Part Numbers currently being processed by the Area.

Undo creates a compensating Movement.

The original Movement always remains part of production history.

# 11. Production Routing

A Route describes the intended manufacturing path of a production quantity.

Routes provide planning and tracking guidance but never dictate what actually happens on the shop floor.

Production always reflects reality. Routes exist to visualize, monitor, and improve the manufacturing process.

---

## Route Template

A Route Template is a reusable sequence of production Areas.

Example:

```text
Material
→ Cut
→ Lathe
→ Deburr
→ External
→ Stockroom
```

Each Route Step may define:

- Area
- Operation
- Sequence
- Expected Duration
- Instructions
- Preferred Machine (optional)

A Route may visit the same Area multiple times.

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

When production begins, released production quantities may be assigned Routes derived from a Route Template.

Once assigned, the Route becomes an independent snapshot.

Future changes to the original Route Template must never affect production quantities already in production.

Assigned Routes belong to production quantities rather than Production Requests. Different quantities originating from the same Production Request may follow different Routes.

---

## Route Editing

Production does not always follow the planned Route.

Authorized users may modify an assigned Route whenever necessary.

Editing affects only the selected production quantity.

The original Route Template always remains unchanged.

---

## Route Deviation

Production occasionally reaches an unexpected Area.

When this occurs, the application should:

1. Warn the operator.
2. Require confirmation when configured.
3. Update the assigned Route to reflect the actual production path.
4. Preserve the original Route in the audit history.
5. Record the user, timestamp, and reason for the change.

The application should always represent the actual production process rather than forcing production to match the original plan.

---

## Expected Duration

Each Route Step may define an expected processing duration.

Expected duration is used to identify:

- overdue work,
- bottlenecks,
- excessive queue time,
- excessive processing time,
- estimated completion.

Expected duration is advisory only.

It must never block production.

---

# 12. Production Movement

Production Movement is the foundation of PartFlow.

Every meaningful production activity generates a Movement.

Current production state is derived from recorded Movements together with active Production Requests whenever applicable.

---

## Immutable History

Movement history is immutable.

Normal application workflows must never delete production history.

Corrections always create additional Movement records.

---

## Movement Types

Initial Movement types include:

- Receive
- Transfer
- Complete
- Undo
- Quantity Adjustment
- Route Adjustment

Additional Movement types may be introduced in the future without changing the overall tracking model.

---

## Movement Information

Each Movement should record enough information to completely reconstruct production history.

Typical information includes:

- Production Request
- Part
- Quantity
- Source Area
- Destination Area
- Operation
- Source Machine
- Destination Machine
- Route Step
- Worker
- Station
- Scan Timestamp
- Server Timestamp
- Movement Type
- Reason
- Device Event ID (offline)

Current production state should always be derivable from Movement history.

---

## Quantity Integrity

Movement processing must preserve production quantity.

The application must never:

- create quantity,
- destroy quantity,
- duplicate quantity,
- produce negative quantity.

Any quantity adjustment must generate an explicit Movement.

---

## Undo

Undo exists solely to correct recent scanning mistakes.

Undo creates a compensating Movement.

The original Movement always remains part of production history.

---

# 13. Worker Sessions

Worker identification is configurable per Area.

Supported modes include:

- No Worker identification
- Fixed Worker assignment
- Worker barcode scanning

Worker identity exists for accountability rather than production logic.

---

## Worker Session

When Worker scanning is enabled, scanning a Worker barcode starts a Worker Session.

Subsequent production scans automatically use the active Worker until:

- another Worker signs in,
- the session expires,
- or the Worker signs out.

The active Worker should always be clearly visible on the Scan Station.

---

## Machine Selection

Areas containing multiple Machines require an active Machine selection. Areas without Machines do not require Machine selection.

The selected Machine remains active across subsequent scans until changed or cleared.

This minimizes repetitive scanning while maintaining accurate machine tracking.

---

# 14. Offline Operation

Production should continue during temporary network outages.

Scan Stations continue accepting scans while disconnected.

Operators should not need to change their workflow because connectivity is temporarily unavailable.

---

## Offline Queue

Each Scan Station maintains a local queue of pending production events.

Every locally created event should include:

- Device Event ID
- Device ID
- Local Timestamp
- Local Sequence Number
- Part
- Quantity
- Area
- Operation
- Machine
- Worker
- Event Type

Synchronization begins automatically when connectivity returns.

---

## Synchronization

When reconnecting, the application should:

1. Upload events in local sequence order.
2. Reject duplicate Event IDs.
3. Preserve the original scan timestamp.
4. Record the server receive timestamp separately.
5. Revalidate production state.
6. Detect quantity or routing conflicts.

Synchronization must never silently overwrite production history.

---

## Conflict Resolution

Offline synchronization may produce conflicts.

Examples include:

- quantity already consumed,
- conflicting adjustments,
- duplicate uploads,
- incompatible movement order.

The application must never invent a resolution.

Instead, conflicting production should be flagged for Manager review.

---

## Idempotency

Offline synchronization must be idempotent.

Uploading the same offline event multiple times must never create duplicate production history.

---

# 15. Roles and Permissions

PartFlow uses role-based authorization.

Permissions should closely reflect real manufacturing responsibilities.

---

## Administrator

Administrators manage the system.

Typical responsibilities include:

- Users
- Roles
- Departments
- Areas
- Operations
- Machines
- Barcode configuration
- Route Templates
- Worker policies
- Offline policies
- System settings

Administrators have unrestricted access to production data.

---

## Manager

Managers supervise production.

Typical responsibilities include:

- Purchase Orders
- Production Requests
- Priorities
- Routing
- Quantity adjustments
- Allocation corrections
- Historical corrections
- Reporting
- Offline conflict resolution

Managers improve production visibility without becoming part of normal production scanning.

---

## Operator

Operators perform production work.

Typical responsibilities include:

- Select Machine
- Identify themselves when required
- Scan Parts
- Confirm quantities
- Receive production
- Complete production
- Modify completion allocation
- Undo recent mistakes when permitted

Operators should be able to complete normal production without requiring Manager assistance.

# 16. Application Views

The application provides separate user experiences for production, monitoring, management, and administration.

Each interface should remain focused on its primary purpose.

Production interfaces should optimize speed and simplicity.

Management interfaces should prioritize visibility and control.

Administrative interfaces should remain isolated from day-to-day production.

---

## Scan Station

The Scan Station is a fixed production interface assigned to a single Area.

It is optimized for:

- Android tablets
- Touchscreen PCs
- Barcode scanners
- Keyboard wedge scanners

Normal production should occur on a single screen without navigation.

Typical information includes:

- Department
- Area
- Operation
- Selected Machine
- Active Worker
- Large barcode input
- Last scanned Part
- Quantity input (when required)
- Success, warning, and error feedback
- Recent scan history
- Current Area inventory
- Undo Last Scan

The barcode input should automatically regain focus after every completed operation.

---

## Production Board

The Production Board is a read-only dashboard designed for large shared displays.

Its purpose is to provide real-time visibility into current production across the Department.

Typical information includes:

- Part Number
- Current Location
- Quantity
- Request Numbers
- Due Date
- Days Remaining
- Time in Current Area
- Priority

When production is distributed across multiple locations, the display should clearly show the distribution.

Example:

```text
Cut (4)
Lathe 1 (2)
Lathe 3 (4)
```

The display should remain readable from a distance.

Priority should be noticeable without becoming visually distracting.

Long lists may automatically rotate between pages.

---

## Area Board

The Area Board provides a focused view of production inside individual Areas.

Each Area displays:

- Current production quantities
- Operation
- Machine distribution
- Due dates
- Priority
- Search
- Sorting

Example:

```text
PF-BRACKET-003

Qty 10

PO-2026-00128

Lathe 1 (4)
Lathe 2 (6)

12 days remaining
```

---

## Tracking

Tracking is the primary management interface.

Managers should be able to search and filter production using:

- Part Number
- Purchase Order
- Request Number
- External Job Number
- Requester
- Area
- Operation
- Machine
- Request Type
- Status
- Due Date

Selecting a Production Request should provide:

- Production Request information
- Current production state
- Quantity distribution
- Assigned Route
- Movement history
- Completion status

Authorized users may also perform production corrections from this interface.

---

## Administration

Administrative pages configure the system rather than production.

Typical configuration includes:

- Departments
- Areas
- Machines
- Workers
- Users
- Roles
- Permissions
- Route Templates
- Barcode configuration
- Worker policies
- Offline behavior
- Application settings

Administrative operations should remain separate from production workflows.

---

# 17. ERP Boundary

ERP and PartFlow have different responsibilities.

ERP owns business planning.

PartFlow owns production tracking.

The two systems exchange information but remain independently functional.

---

## ERP Responsibilities

ERP is responsible for:

- Customer Orders
- Purchasing
- Inventory
- Business Planning
- Business Reporting

ERP is the source of Purchase Orders and Part master data.

---

## PartFlow Responsibilities

PartFlow is responsible for:

- Production Requests
- Quantity Distribution
- Production Routing
- Movement History
- Current Production Visibility
- Completion Tracking
- Production Reporting

Internal concepts such as Rework and Modification belong exclusively to PartFlow.

---

## Integration Principles

ERP integration should follow these rules:

- The application must function without ERP connectivity.
- ERP identifiers remain separate from internal identifiers.
- ERP imports should be idempotent.
- ERP response formats must not leak into the domain model.
- Production history is owned exclusively by PartFlow.

---

# 18. Audit and Data Integrity

Production tracking exists to represent reality.

The integrity of production data is more important than convenience.

---

## Audit Trail

The application should preserve a complete audit trail for:

- Purchase Order creation
- Production Request creation
- Quantity adjustments
- Completion allocation
- Route assignment
- Route modifications
- Priority changes
- Area transfers
- Machine transfers
- Worker sessions
- Undo operations
- Offline synchronization
- Administrative configuration changes

Historical production records should never disappear.

---

## Core Integrity Rules

The following rules should always remain true.

### Quantity Must Be Conserved

Production quantity must never be accidentally created, destroyed, duplicated, or lost.

---

### History Must Be Preserved

Historical production activity represents factual shop-floor events.

Corrections create new history.

They never rewrite previous events.

---

### Production Reflects Reality

The application adapts to actual production.

Production should never be forced to match software expectations.

---

### Ambiguity Requires Confirmation

Whenever production intent cannot be determined with sufficient confidence, the application must require confirmation before modifying production data.

---

## Database Integrity

The underlying data model should protect important business constraints whenever practical.

Examples include:

- Non-negative quantities
- Valid Area and Machine relationships
- Unique Request Numbers
- Unique offline Event IDs
- Idempotent synchronization
- Immutable completed Purchase Orders

Business rules should be enforced consistently regardless of the client application.

---

# 19. Initial Scope

The first production release should support:

- Machine Shop
- Part master records
- Manual Purchase Orders
- File-based Purchase Order import
- Internal Production Requests
- Rework
- Modification
- Areas
- Operations
- Optional Machines
- Barcode scanning
- Optional Worker identification
- Linear Routes
- Quantity distribution
- Completion allocation
- Scan Station
- Production Board
- Area Board
- Tracking
- Immutable Movement history
- Undo
- Offline scan queue
- Role-based authorization

ERP synchronization may be added later without changing the core domain model.

---

# 20. Future Scope

The following capabilities intentionally remain outside the initial release:

- Individual piece tracking
- Full ERP synchronization
- Machine automation
- Production scheduling
- Inventory management
- Cost accounting
- Payroll
- IoT integration
- Predictive analytics
- Quality management
- General workflow engine

Future features should extend the existing production model rather than replace it.

---

# 21. Guiding Principles

Every future feature should reinforce the following principles.

- Scanner-first interaction.
- Production follows reality.
- Production Requests represent production demand.
- Quantities represent production state.
- Complete and immutable movement history.
- Practical shop-floor workflows.
- ERP independence.
- Accurate quantity tracking.
- Clear production visibility.
- Long-term maintainability.

Whenever a future design decision conflicts with these principles, the simpler and more production-oriented solution should be preferred.
