# PartFlow Project Profile

> **Version:** Draft 1
> **Status:** Living Document

---

# 1. Project Overview

## Purpose

PartFlow is an internal shop-floor tracking system designed to monitor CNC parts as they move through the manufacturing process.

Its primary objective is to provide real-time visibility into:

* where every part is currently located,
* how much quantity is being processed,
* which machine is working on it,
* what work remains,
* and when it is expected to be completed.

PartFlow is intentionally focused on production tracking rather than business management. It is **not** an ERP, MES, inventory system, or scheduling system, although it may integrate with those systems in the future.

The first deployment targets the **Machine Shop** department. The architecture should remain flexible enough to support additional departments such as Purchasing, Assembly, Production, or Quality Control without changing the core domain model.

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

Whenever there is a trade-off, practical day-to-day usability should take precedence over theoretical perfection.

The application should help operators perform their work with as few interactions as possible while still preserving reliable production data.

---

# 3. Design Principles

## Scanner First

The primary interaction method is a barcode scanner.

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

Corrections should create additional history entries rather than deleting previous records.

The application should always preserve a complete audit trail of production activities.

---

## ERP Independent

The application must function without ERP integration.

ERP data may be imported manually today and synchronized automatically in the future.

Business rules inside PartFlow must not depend on ERP-specific concepts or APIs.

---

## Practical Before Perfect

Real manufacturing environments contain exceptions.

The application should assist operators in handling unexpected situations rather than rejecting every deviation.

Whenever ambiguity exists, the system should require confirmation instead of making assumptions.

---

# 4. Terminology

This section defines the vocabulary used throughout the project.

These terms should be used consistently in documentation, source code, database design, and user interfaces.

---

## Purchase Order (PO)

A Purchase Order represents manufacturing work received from the ERP system.

A Purchase Order contains one or more requested Parts.

Each Purchase Order has its own PO Number.

A PO Number has no fixed format and should always be treated as an arbitrary string.

A Purchase Order is considered complete only after every requested quantity has been received into Stockroom.

Completed Purchase Orders are moved to History and cannot be reopened.

---

## Part

A Part represents a reusable product definition identified by its Part Number.

A Part Number:

* is unique,
* originates from the ERP system,
* has no predefined format,
* remains unchanged even when drawing revisions change,
* is the value encoded in the reusable folder barcode.

A Part may appear simultaneously in multiple active Purchase Orders.

The drawing folder belongs to the Part Number rather than to any specific Purchase Order.

---

## Request

A Request represents production demand for a Part.

Requests may originate from:

* an ERP Purchase Order,
* an internal New request,
* a Rework request,
* or a Modification request.

Although operators only scan the Part Number barcode, Requests allow the system to distinguish multiple active demands for the same Part Number.

A Request records information such as:

* requested quantity,
* due date,
* requester,
* priority,
* work type,
* current status,
* completion status.

Requests are business objects.

They are not exposed through barcodes.

---

## Work Type

Work Type describes why production work exists.

Initial Work Types are:

* New
* Rework
* Modification

ERP Purchase Orders always create **New** Requests.

Rework and Modification Requests exist only inside PartFlow and are not synchronized back to the ERP system.

---

## Department

A Department represents a major organizational unit.

Examples include:

* Machine Shop
* Purchasing
* Assembly
* Production
* Quality Control

The first deployment of PartFlow only targets the Machine Shop department.

---

## Area

An Area represents a physical production area where work is received, processed, or transferred.

Examples:

* Material
* Cut
* Lathe
* Mill
* Manual
* Deburr
* External
* Stockroom

An Area may contain one or more Machines.

Each Area has its own identity, display color, icon, and configuration.

---

## Machine

A Machine represents an individual production resource inside an Area.

For example:

Area: Lathe

Machines:

* Lathe 1
* Lathe 2
* Lathe 3
* Lathe 4

Each Machine has its own barcode.

An Area may use a single scanner and tablet while supporting multiple Machines.

---

## Worker

A Worker represents the operator performing production activities.

Worker identification may be optional or mandatory depending on the Area configuration.

When enabled, Workers identify themselves by scanning their personal barcode before scanning Parts.

---

# 5. Core Domain

The following concepts form the core business model of PartFlow.

These concepts describe **what** the system tracks rather than **how** it is implemented.

---

## Part-Centric Tracking

The Part Number is the central identity used throughout production.

The reusable folder barcode always represents a Part Number.

The folder itself is reused across different Purchase Orders and drawing revisions.

A single drawing folder may support production work for multiple Requests over time.

When the same Part Number is requested again while an earlier quantity is still being processed, the new Request is not automatically started.

Instead, it enters a waiting state until a manager decides how the new demand should be handled.

---

## Shared Drawing Folder

The drawing folder is primarily required during machine setup.

After setup has been completed, operators generally no longer need continuous access to the folder.

Because of this, the same drawing folder may be shared between multiple production Areas whenever necessary.

The physical location of the folder is independent from the production quantities being tracked.

PartFlow therefore tracks production quantities instead of attempting to track ownership of the drawing folder itself.

---

## Production State

At any moment, the system should be able to answer:

* How many quantities are currently active?
* Which Areas contain those quantities?
* Which Machines are processing them?
* Which Requests still require completion?
* Which Purchase Orders are affected?

The application tracks the current production state through movement history rather than by manually updating status fields.

# 6. Barcode Model

Barcode scanning is the primary interaction method throughout PartFlow.

Every barcode must represent a well-defined business object.

The application should determine the barcode type automatically without requiring operators to manually select the scan mode.

The exact barcode format is implementation-specific and may evolve over time. The business meaning of each barcode, however, must remain stable.

---

## Supported Barcode Types

The system initially supports the following barcode categories:

* Part
* Area
* Machine
* Worker
* Work Type
* Administrative Commands (future)

Additional barcode types may be introduced in the future without changing the production workflow.

---

## Part Barcode

The Part barcode represents a reusable Part Number.

It is attached to the drawing folder rather than to the physical parts.

The same barcode may continue to be used for many years across multiple Purchase Orders, drawing revisions, and production runs.

Only one barcode should normally exist for a given Part Number.

---

## Area Barcode

Each production Area has its own barcode.

Area barcodes are primarily used during setup or administration.

Normal production scanning should not require repeatedly scanning the Area because each Scan Station is permanently assigned to one Area.

---

## Machine Barcode

Each Machine has its own barcode.

When an Area contains multiple Machines, the active Machine is selected by scanning its barcode.

Subsequent Part scans automatically belong to the currently selected Machine until:

* another Machine is selected,
* the Machine is cleared,
* or the Machine session expires.

Areas containing only one Machine may automatically select it.

---

## Worker Barcode

Worker identification is configurable.

Depending on the Area configuration:

* Worker scanning may be disabled.
* A default Worker may be assigned.
* Operators may be required to identify themselves before scanning Parts.

Worker identity should never affect production logic.

It exists only for accountability and reporting.

---

## Work Type Barcode

Work Type barcodes allow operators to create internal Requests without navigating through menus.

Initial Work Type barcodes include:

* New
* Rework
* Modification

The order of scanning is not important.

For example:

Part → Rework

and

Rework → Part

should produce the same result.

---

# 7. Quantity Distribution

PartFlow tracks production by quantity.

The application does not track individual physical pieces.

At any time, a Part may have quantities distributed across multiple Areas and Machines.

Example:

```text
Part Number

PF-BRACKET-001

Current Distribution

Material      5
Cut           4
Lathe 1       3
Lathe 2       2
Mill          6
```

The sum of every active quantity always represents the total quantity currently in production.

---

## Quantity Splitting

Production quantities may be divided at any time.

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

The application should preserve the complete distribution while keeping the workflow simple for operators.

---

## Quantity Merging

Multiple production quantities belonging to the same Part Number may later be merged into one Area or Machine.

Example:

```text
Lathe 1    2
Lathe 2    4
```

↓

```text
Mill 6
```

The system should preserve movement history while presenting only the current distribution to users.

---

## Current Production State

The application should always be able to determine:

* Total active quantity.
* Quantity at every Area.
* Quantity at every Machine.
* Quantity currently waiting.
* Quantity completed.
* Quantity still required.

This information is derived from production movements rather than manually maintained counters whenever possible.

---

# 8. Production Requests

Production work begins with one or more Requests.

Requests represent production demand rather than physical production state.

Examples:

* ERP Purchase Order
* Internal New Request
* Rework Request
* Modification Request

Multiple Requests may exist simultaneously for the same Part Number.

---

## Queued Requests

When a new Request is created while production of the same Part Number is already active, the Request normally enters a waiting state.

Example:

```text
PF-BRACKET-001

Running

PO-1001
Quantity: 10

Queued

PO-1008
Quantity: 5

Queued

Rework
Quantity: 2
```

Requests remain independent even though they share the same Part Number.

Managers may later decide to:

* start a separate production quantity,
* combine quantities,
* delay production,
* or reprioritize queued Requests.

---

## Starting Production

A queued Request begins production when quantity is released into the first production Area.

The initial Area is typically Material.

Future routing rules may define different starting Areas.

---

# 9. Completion Allocation

Production quantities and Requests intentionally remain independent during production.

The application tracks:

* where quantities are,
* and what Requests still require completion.

These two views are connected only when production quantities are completed.

---

## Automatic Suggestion

When completed quantities enter Stockroom, the application should suggest how those quantities should be allocated to outstanding Requests.

The allocation policy is configurable.

Possible strategies include:

* Earliest Due Date
* Highest Priority
* FIFO
* Manual

The suggested allocation serves only as an initial proposal.

---

## Operator Confirmation

The receiving operator may accept or modify the suggested allocation before confirming completion.

Example:

```text
Completed Quantity

6
```

Suggested Allocation:

```text
PO-1001    4

PO-1008    2
```

The operator may adjust the allocation when the actual production situation differs from the suggestion.

The total allocated quantity must always equal the completed quantity.

---

## Manager Responsibilities

Managers are not required during normal receiving operations.

Instead, they may:

* review completed allocations,
* correct mistakes,
* resolve disputes,
* modify historical allocation records when authorized.

This keeps production flowing without requiring manager intervention for every receiving transaction.

---

# 10. Core Production Workflow

The production workflow intentionally mirrors the physical movement of parts throughout the shop.

The application should never require operators to perform administrative work that does not exist in the real manufacturing process.

---

## New ERP Purchase Order

1. Import or manually enter the Purchase Order.
2. Create Requests for every requested Part.
3. Place the Requests into the production queue.
4. Review routing if necessary.
5. Release production when appropriate.

---

## Internal Request

When a scanned Part Number does not belong to active production, the operator may create an internal Request.

Examples include:

* New
* Rework
* Modification

Required information:

* Quantity
* Requester
* Reason

Optional information:

* Due Date
* Notes

The application records:

* creation time,
* current Area,
* current Machine,
* Worker, when available.

---

## Receiving Production

When production quantities arrive at an Area:

1. Select the active Machine if necessary.
2. Scan the Part barcode.
3. Confirm quantity when required.
4. Review warnings if any.
5. Record the movement.
6. Refresh production status.

The application should normally complete this workflow without additional button presses.

---

## Completing Production

When quantities reach Stockroom:

1. Scan the Part.
2. Enter completed quantity if necessary.
3. Review suggested Request allocation.
4. Adjust allocation when required.
5. Confirm.
6. Record completion.
7. Update Request completion status.
8. Check whether affected Purchase Orders are now complete.
9. Move completed Purchase Orders to History automatically.

---

## Undo

Operators may undo recent scanning mistakes when permitted.

Undo creates a compensating movement rather than deleting historical records.

The production history must remain complete and auditable.

# 11. Production Routing

A Route describes the intended manufacturing path of a production Request.

Routes define where work is expected to be performed but do not control actual production.

Production always follows real shop-floor activities. Routes serve as planning and tracking references.

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

Templates are created and maintained by authorized users.

They are intended to reduce repetitive setup when similar Parts follow the same manufacturing process.

---

## Assigned Route

When a Request begins production, a Route Template may be assigned to it.

Once assigned, the Route becomes independent from the original template.

Later changes to the Route Template must never modify Requests that are already in production.

---

## Route Editing

Production does not always follow the planned Route.

Authorized users may edit an assigned Route at any time.

Editing a Route affects only the selected Request.

It must never modify the original Route Template.

---

## Route Deviation

Operators may occasionally receive production at an Area that is different from the expected next step.

When this occurs, the application should:

* warn the operator,
* allow confirmation when permitted,
* update the assigned Route to match the actual production path,
* preserve the previous Route in the audit history.

The application should always represent the actual production process rather than forcing production to match the original plan.

---

## Expected Duration

Each Route step may define an expected processing duration.

Expected duration is used to:

* identify bottlenecks,
* detect overdue production,
* estimate completion,
* support production reporting.

Expected duration is advisory only.

It must never block production.

---

# 12. Production Movement

Production movement is the foundation of PartFlow.

Every meaningful production event should generate a Movement record.

Current production state is derived from these records.

---

## Immutable History

Movement history is immutable.

Normal application workflows must never delete production history.

Corrections are recorded as new Movement records.

---

## Movement Types

Initial Movement types include:

* Receive
* Transfer
* Complete
* Undo
* Quantity Adjustment
* Route Adjustment

Additional Movement types may be introduced in future versions without changing the overall tracking model.

---

## Current Production State

The application derives the current production state from recorded Movements.

The system should always be able to determine:

* current Area,
* current Machine,
* current quantity distribution,
* completed quantity,
* remaining quantity,
* production history.

Current state should never depend solely on manually edited status fields.

---

## Quantity Integrity

The application must preserve quantity integrity at all times.

Production movements must never:

* create quantity,
* destroy quantity,
* duplicate quantity,
* produce negative quantity.

Any intentional quantity adjustment must be explicitly recorded.

---

## Undo

Undo is intended to correct recent scanning mistakes.

Undo creates a compensating Movement.

The original Movement remains part of production history.

---

# 13. Worker Sessions

Worker identification is configurable.

Each Area may independently choose how Workers are identified.

Supported modes include:

* no Worker identification,
* fixed Worker assignment,
* Worker barcode scanning.

---

## Worker Session

When Worker identification is enabled, scanning a Worker barcode starts a Worker Session.

Subsequent production scans automatically use the active Worker until:

* another Worker signs in,
* the session expires,
* or the Worker signs out.

Worker Sessions exist only for accountability.

Production logic should never depend on Worker identity.

---

## Machine Selection

Areas containing multiple Machines require an active Machine selection.

The selected Machine remains active for subsequent scans until changed.

This minimizes repetitive scanning while preserving accurate machine tracking.

---

# 14. Offline Operation

Production should continue during temporary network outages.

Scan Stations should continue accepting scans while disconnected.

---

## Offline Queue

Each Scan Station maintains a local queue of pending production events.

Events are synchronized automatically when connectivity returns.

Operators should not need to manually retry synchronization.

---

## Event Ordering

Each locally recorded event preserves:

* scan time,
* device identity,
* local event order.

Server synchronization should preserve the original production sequence whenever possible.

---

## Conflict Resolution

Offline synchronization may occasionally produce conflicts.

Examples include:

* quantity already consumed,
* conflicting adjustments,
* duplicate uploads.

The application must never silently resolve production conflicts.

Instead, conflicts should be presented for review by authorized users.

---

## Idempotency

Synchronization must be idempotent.

Uploading the same offline event multiple times must never create duplicate production history.

---

# 15. Roles and Permissions

PartFlow uses role-based authorization.

Permissions should remain simple and closely aligned with real shop-floor responsibilities.

---

## Administrator

Administrators manage the application.

Typical responsibilities include:

* system configuration,
* Area management,
* Machine management,
* Worker management,
* user management,
* permission management,
* routing templates,
* barcode configuration,
* application settings.

Administrators have unrestricted access to production data.

---

## Manager

Managers supervise production.

Typical responsibilities include:

* Purchase Orders,
* Requests,
* priorities,
* routing,
* production corrections,
* quantity adjustments,
* historical corrections,
* reporting.

Managers should improve production visibility without becoming part of the normal production workflow.

---

## Operator

Operators perform production work.

Typical responsibilities include:

* selecting Machines,
* identifying themselves when required,
* scanning Parts,
* confirming quantities,
* receiving production,
* completing production,
* confirming or modifying completion allocation,
* undoing recent mistakes when permitted.

Operators should be able to complete normal production without requiring Manager assistance.

---

# 16. Application Views

The application consists of four primary user experiences.

---

## Scan Station

A fixed production interface assigned to one Area.

Optimized for:

* tablets,
* touch screens,
* barcode scanners,
* keyboard wedge scanners.

The Scan Station should remain on a single screen during normal production.

---

## Production Board

A read-only production display intended for large shared monitors.

Its purpose is to provide real-time production visibility across the Department.

The display should remain easy to read from a distance.

---

## Tracking

The primary management interface.

Tracking provides search, filtering, reporting, production history, routing information, quantity distribution, and production analytics.

---

## Administration

Administrative pages configure the application rather than production.

Configuration should remain separate from day-to-day production activities.

---

# 17. ERP Boundary

ERP and PartFlow have different responsibilities.

ERP owns business planning.

PartFlow owns production tracking.

The two systems exchange information but remain independently functional.

---

## ERP Responsibilities

ERP is responsible for:

* customer orders,
* purchasing,
* inventory,
* planning,
* business reporting.

---

## PartFlow Responsibilities

PartFlow is responsible for:

* production Requests,
* quantity distribution,
* production routing,
* movement history,
* current production visibility,
* production reporting.

Internal production concepts such as Rework and Modification belong exclusively to PartFlow.

---

# 18. Data Integrity

PartFlow is built around preserving production truth.

The following rules must always remain true.

---

## Quantity Must Be Conserved

The application must never lose production quantity.

Every quantity adjustment must be intentional and traceable.

---

## History Must Be Preserved

Production history represents factual shop-floor activity.

History should never be rewritten simply to produce cleaner data.

---

## Production Reflects Reality

The application should adapt to real production.

Operators should never be forced to change production simply because software expected a different workflow.

---

## Ambiguity Requires Confirmation

Whenever production intent cannot be determined with confidence, the application should request confirmation rather than making assumptions.

---

# 19. Future Scope

The initial release intentionally excludes:

* production scheduling,
* inventory management,
* costing,
* payroll,
* ERP synchronization,
* machine automation,
* IoT integration,
* quality management,
* predictive analytics.

These capabilities may be introduced later without changing the core production model.

---

# 20. Guiding Principles

PartFlow is built around a small number of fundamental principles.

Every future feature should reinforce these principles rather than weaken them.

* Scanner-first interaction.
* Quantity-based production tracking.
* Real production over theoretical workflows.
* Complete and immutable movement history.
* Practical shop-floor operation.
* Simple operator experience.
* Clear production visibility.
* ERP independence.
* Accurate quantity tracking.
* Long-term maintainability.
