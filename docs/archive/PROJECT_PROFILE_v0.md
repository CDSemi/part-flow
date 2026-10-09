# CNC Part Flow — Project Profile

## 1. Project Overview

CNC Part Flow is an internal shop-floor tracking application for monitoring parts as they move through the manufacturing process.

The initial deployment will serve the Machine Shop department. The system may later be extended to Material Purchasing, Assembly, Production, and other departments, but it is not intended to become a full ERP system.

The application must remain focused on:

* Purchase order intake
* Internal work requests
* Part quantities
* Production routing
* Current part locations
* Machine assignments
* Barcode scan events
* Priority management
* Movement history
* Production visibility
* Completion reporting

ERP integration will be introduced later. The initial version must work independently with manual entry and file import.

---

## 2. Operational Context

Each reusable part design is identified by a Part Number.

A physical folder containing the part drawings is maintained for each Part Number. The folder is reusable across purchase orders and drawing revisions.

The Part Number barcode is attached to the folder rather than to the physical parts because:

* Parts begin as raw material and undergo machining.
* A barcode attached directly to a part would not survive the manufacturing process.
* Attaching labels to every individual piece is operationally impractical.
* Drawings may be replaced by newer revisions.
* The same folder may be reused for future purchase orders.
* The company does not have sufficient labor to label and track every physical piece independently.

Parts normally travel with their drawing folder. However, the system must support quantities of the same Part Number being split across multiple production areas.

The system therefore tracks quantities and batches rather than individual physical pieces.

---

## 3. Core Domain Concepts

### 3.1 Part

A `Part` is the reusable master definition of a manufactured item.

A Part is identified by its Part Number.

A Part Number:

* Is provided by the ERP system.
* Has no fixed format.
* Must be treated as an arbitrary string.
* May appear in multiple active purchase orders.
* Does not change when the drawing revision changes.
* Is the value encoded in the reusable folder barcode.

Suggested attributes:

* Internal ID
* Part Number
* Name
* Description
* Image or icon
* Current drawing revision, when available
* ERP reference, when available
* Active status

The internal ID is never required on the physical barcode.

---

### 3.2 Purchase Order

A `PurchaseOrder` represents work received from the ERP system.

All ERP purchase orders are treated as new manufacturing work, even when the actual work may reuse or modify an older physical part.

Suggested attributes:

* Internal ID
* PO Number
* Received Date
* Source
* Status
* Completion Date
* Notes

A PO Number has no required format and must be treated as an arbitrary string.

A Purchase Order is complete only when every associated Part Request has reached Stockroom.

Once completed:

* The Purchase Order is moved to History.
* It is removed from active production views.
* It cannot be reopened.
* Additional work must be represented by a new Purchase Order or internal request.

---

### 3.3 Part Request

A `PartRequest` represents a specific quantity of a Part requested for production.

A Part Request may originate from:

* An ERP Purchase Order
* A new internal request
* A rework request
* A modification request

This entity is required even though operators scan only the Part Number. It allows the system to distinguish multiple active requests for the same Part Number.

Suggested attributes:

* Internal ID
* Part ID
* Purchase Order ID, optional for internal requests
* Work Type
* Requested Quantity
* Due Date, optional
* Received Date
* Requester
* Reason
* Priority
* Status
* Job Numbers
* Assigned Route
* Completion Date

The internal Part Request ID is not printed on the folder and is not part of the normal scan workflow.

---

### 3.4 Work Type

`WorkType` describes the nature of a Part Request.

Initial values:

* New
* Rework
* Modification

ERP Purchase Orders always create `New` requests.

Rework and Modification are internal department requests and do not require an ERP Purchase Order.

An internal request records the context captured when the request is created:

* Received time
* Quantity
* Requester
* Reason
* Due date, optional
* User who created the request

Multiple active Rework or Modification requests may exist for the same Part Number.

---

### 3.5 Work Batch

A `WorkBatch` represents a movable quantity of a Part currently traveling through production.

It is not an individually labeled physical object. It is a logical production batch used to track quantity distribution.

A Work Batch belongs to one Part Number and contains quantity allocated from one or more Part Requests.

This distinction is necessary because:

* The same Part Number may exist in multiple active Purchase Orders.
* New demand may be added to a quantity already in production.
* A quantity may be split across multiple Areas.
* A partial quantity may be sent for rework.
* Several requests may be processed together as one practical shop-floor batch.

Suggested attributes:

* Internal ID
* Part ID
* Quantity
* Current Area
* Current Machine, optional
* Current Route Step
* Status
* Created Time
* Completed Time

A batch may be split when only part of its quantity moves to another Area.

Example:

```text
Part Number: PF-BRACKET-001
Total active quantity: 10

Cut:   4
Lathe: 6
```

The UI should show the distributed quantities explicitly rather than using ambiguous notation such as `10 (-2)`.

---

### 3.6 Batch Allocation

A `BatchAllocation` associates Work Batch quantities with their originating Part Requests.

Example:

```text
PO-1001 requests 6 pieces of PART-A
PO-1002 requests 4 pieces of PART-A

A manager combines them into one 10-piece production batch.
```

The Work Batch quantity is 10, while its allocations remain:

```text
PO-1001: 6
PO-1002: 4
```

This allows quantities to be processed together without losing Purchase Order ownership.

The application should hide this complexity from operators unless a selection or correction is required.

---

### 3.7 Job Number

A Part Request may have one or more Job Numbers.

A Job Number is unique across the system.

Job Numbers are business references and do not need to be encoded into the Part Number barcode.

---

## 4. Organizational Model

### 4.1 Department

A `Department` is a major organizational unit.

Initial example:

* Machine Shop

Future examples may include:

* Purchasing
* Assembly
* Production
* Stockroom

---

### 4.2 Area

An `Area` is a production location or functional processing area within a Department.

Examples:

* Material
* Cut
* Lathe
* Mill
* Manual
* Deburr
* External
* Stockroom

Suggested attributes:

* Internal ID
* Barcode
* Name
* Description
* Department
* Display Color
* Icon
* Sort Order
* Active Status
* Scan Settings

The Area barcode and internal identity are immutable.

The Area name, color, icon, and description may be changed by an administrator.

`Area` is preferred over `Stage` because an Area is a reusable physical or operational location, while a stage is a position within a specific route.

---

### 4.3 Machine

A `Machine` is a trackable resource within an Area.

Example:

```text
Area: Lathe

Machines:
- Lathe 1
- Lathe 2
- Lathe 3
- Lathe 4
```

Suggested attributes:

* Internal ID
* Barcode
* Name
* Area
* Description
* Active Status

Each Machine has its own barcode.

An Area may use one shared scanner and tablet for multiple Machines.

When an Area contains multiple Machines:

1. The operator selects or scans the Machine.
2. Subsequent Part Number scans are assigned to that Machine.
3. The selected Machine remains active until changed, cleared, or timed out.

When an Area has only one Machine or one fixed operator position, the system may automatically select it and eliminate the Machine scan.

---

## 5. Barcode Model

### 5.1 Barcode Types

The system must recognize multiple barcode types:

* Part Number
* Area
* Machine
* Worker
* Work Type command
* Administrative command, when necessary

Barcode values should use typed prefixes where practical.

Example:

```text
PART:PF-BRACKET-001
AREA:LATHE
MACHINE:LATHE-03
WORKER:000184
TYPE:REWORK
TYPE:MODIFICATION
```

The barcode payload must not expose database IDs when a stable business identifier is available.

---

### 5.2 Part Number Resolution

The folder barcode identifies only the Part Number.

When the Part Number has one unambiguous active production context, the system may proceed automatically.

When multiple active contexts exist, the system must not guess.

It must present relevant choices such as:

* Existing active Purchase Order
* Existing internal request
* Existing Work Batch
* Start a new internal New request
* Start a Rework request
* Start a Modification request
* Cancel

Choices should be filtered by the current Area and current production context so the most likely action appears first.

The application may provide configurable defaults, but uncertain input must never update tracking data automatically.

---

## 6. Request Intake

### 6.1 ERP Purchase Order Intake

A Purchase Order may initially be entered by:

* Manual entry
* File import
* Future ERP API synchronization

When an ERP Purchase Order is received:

1. Create or reuse the Part master record.
2. Create one Part Request for each PO Part entry.
3. Set the Work Type to New.
4. Place the request in the Material Queue.
5. Check whether the same Part Number already has active production quantities.

If the Part Number is already active, the new request may remain queued.

A manager may later decide to:

* Start a separate Work Batch.
* Add the new quantity to an existing compatible Work Batch.
* Partially add the quantity to an existing batch.
* Leave the request queued until the current batch is completed.

Combining quantities must preserve the allocations to their original Part Requests and Purchase Orders.

---

### 6.2 Internal Request Intake

When a scanned Part Number does not match an appropriate active request, the user may create an internal request.

Available Work Types:

* New
* Rework
* Modification

The user must enter:

* Quantity
* Requester
* Reason

The user may enter:

* Due Date
* Notes

The system records automatically:

* Received timestamp
* Creating user or station
* Current Area
* Current Machine, when applicable

An internal temporary PO-like grouping may be generated for reporting, for example:

```text
PO202606031525-NEW
PO202606031529-REWORK
PO202606031535-MOD
```

These generated identifiers are internal organizational references, not ERP Purchase Orders.

Internally, each request must remain individually identifiable even when several requests share the same generated group.

---

## 7. Quantity Tracking

The application tracks quantities, not individual pieces.

A quantity may be:

* Queued
* Combined with another request
* Split into multiple Work Batches
* Distributed across Areas
* Distributed across Machines
* Partially reworked
* Completed at different times

Every movement must specify the moved quantity.

The system must reject any movement that would:

* Move more than the available quantity.
* Produce a negative quantity.
* Lose Purchase Order allocation.
* Duplicate quantity during offline synchronization.
* Complete more quantity than was requested without an explicit adjustment.

Quantity corrections must be recorded as auditable events.

They must not silently overwrite historical values.

---

## 8. Routing

### 8.1 Route Template

A `RouteTemplate` is a reusable linear sequence of production steps.

Example:

```text
Material
→ Cut
→ Lathe
→ Deburr
→ External
→ Stockroom
```

A Route Step may contain:

* Area
* Expected Duration
* Instructions
* Optional preferred Machine
* Sequence Number

A route may visit the same Area more than once.

Example:

```text
Mill
→ External
→ Mill
→ Stockroom
```

---

### 8.2 Assigned Route

When a Route Template is assigned to a Part Request or Work Batch, the application creates an independent route snapshot.

Later changes to the template do not change previously assigned routes.

An assigned route may be edited by an authorized user.

---

### 8.3 Route Deviation

When a Part is scanned into an Area that does not match the next expected Route Step:

1. Show a clear warning.
2. Require confirmation when configured.
3. Update the assigned route to reflect the actual production path.
4. Preserve an audit record of the original route and the change.
5. Record the user, timestamp, reason, and affected step.

The current route should represent the actual intended route after correction.

Historical route changes must remain auditable.

---

### 8.4 Expected Duration

Each Route Step may define an expected duration.

The system uses expected duration to identify:

* Overdue work at an Area
* Potential bottlenecks
* Excessive queue time
* Excessive processing time

For the initial version, time at an Area begins when the batch is scanned into that Area and ends when it is scanned into the next Area.

This measures combined waiting and working time. It does not distinguish actual machine runtime from queue time unless a future start/stop workflow is introduced.

---

## 9. Movement History

A `PartMovement` is an immutable tracking event.

Suggested fields:

* Internal ID
* Work Batch
* Quantity
* Source Area
* Source Machine
* Destination Area
* Destination Machine
* Route Step
* Scan Timestamp
* Server Receive Timestamp
* Worker, optional
* Station
* Movement Type
* Reason
* Related correction event, optional
* Device-generated event ID

Movement types may include:

* Receive
* Transfer
* Split
* Merge
* Complete
* Quantity Adjustment
* Route Correction
* Undo
* Offline Synchronization Correction

Current location and current status are derived from valid movement history and batch state.

Movement records must not be physically deleted through normal application workflows.

---

## 10. Undo and Corrections

The Scan Station may provide an `Undo Last Scan` action.

Undo does not delete the original event.

Instead, it creates a compensating event that restores the previous valid state.

An undo operation must record:

* Original event
* User or station
* Timestamp
* Reason, when required
* Restored Area and Machine
* Restored quantity state

Permissions for undo may be configured by role.

---

## 11. Worker Identification

Worker identification is configurable per Area.

Supported modes:

### No Worker Identification

* Operators do not scan a Worker barcode.
* Movement records may have no Worker.
* The Station identity is still recorded.

### Fixed Worker

* An administrator or manager assigns a default Worker to the Area or Machine.
* Scans automatically use the assigned Worker.

### Worker Scan Required

* The operator scans a Worker barcode before receiving Parts.
* The Worker remains active for the configured session period.
* The UI must clearly show the active Worker.
* The session may expire automatically or be explicitly signed out.

The system must separately record:

* The physical Station
* The selected Machine
* The Worker, when available

---

## 12. Offline Operation

Scan Stations must continue accepting scans during temporary network outages.

Each device maintains a local queue of pending scan events.

Every locally created event must include:

* A device-generated unique event ID
* Device ID
* Local scan timestamp
* Monotonic local sequence number
* Area
* Machine
* Worker
* Part Number
* Quantity
* Selected request or batch context
* Event type

When connectivity returns:

1. Upload pending events in local sequence order.
2. Reject duplicate event IDs idempotently.
3. Preserve the original scan time.
4. Record the server receive time separately.
5. Revalidate events against authoritative server state.
6. Detect quantity, route, and ordering conflicts.
7. Never silently force an invalid movement into the history.

Device timestamps alone must not determine event authority because tablet clocks may be incorrect.

When events from different offline devices conflict, the system must flag the affected batch for reconciliation by a manager rather than inventing an order from timestamps.

Offline support should initially be limited to previously synchronized Parts, Requests, Areas, Machines, Workers, and Routes.

Creating unknown Parts or complex request changes while offline may be restricted.

---

## 13. Priority

`Priority` replaces the term Hot Request.

Priority is an ordered ranking rather than an arbitrary integer edited directly by users.

When a Part Request is marked as Priority:

1. Show the current Department priority list.
2. Add the new request at the bottom by default.
3. Allow an authorized user to reorder requests with drag and drop.
4. Save the ordered list explicitly.
5. Record priority changes in the audit history.

The highest item has the highest operational priority.

Production views sort active work by:

1. Priority rank
2. Due Date
3. Configured fallback order

---

## 14. Roles and Permissions

### Administrator

An Administrator may:

* Access all Departments.
* Manage users and roles.
* Manage Areas and Machines.
* Configure barcodes.
* Configure scan behavior.
* Configure worker identification.
* Configure confirmation requirements.
* Configure offline policies.
* Manage route templates.
* Manage application settings.
* Perform all Manager actions.

### Manager

A Manager may:

* Manage Purchase Orders.
* Manage Part Requests.
* Create internal requests.
* Edit quantities.
* Assign and edit routes.
* Split and combine Work Batches.
* Assign batches to requests.
* Change priorities.
* Correct locations.
* Undo or reverse movements.
* Export and print reports.
* Review exceptions and offline conflicts.

### Operator

An Operator may:

* Identify themselves when required.
* Select or scan a Machine.
* Scan Part Number barcodes.
* Enter quantity when required.
* Confirm ambiguous scans.
* Undo a recent scan when permitted.
* View work assigned to the current Area.

Operators cannot edit Purchase Orders, routes, priorities, or historical records unless explicitly granted additional permission.

---

## 15. Application Views

### 15.1 Scan Station

The Scan Station is a fixed kiosk-oriented screen assigned to one Area.

Primary target:

* Android tablet
* Touchscreen PC
* Barcode scanner
* Keyboard-wedge scanner

The normal scan workflow should not require navigation or mouse interaction.

Main elements:

* Department Name
* Area Name
* Selected Machine
* Active Worker
* Area Description
* Large focused barcode input
* Last scanned Part details
* Quantity input when required
* Clear success, warning, and error feedback
* Recent scan activity
* Current Area inventory
* Undo Last Scan

When an Area contains multiple Machines, a Machine must be selected before scanning Parts.

The scan input should automatically regain focus after every completed action.

---

### 15.2 Production Board

The Production Board is designed for a large shared display.

It shows all active work within a Department.

Suggested columns:

| No. | Part Number | Location | Quantity | Job Number | Due | Days Left | Time in Area |
| --: | ----------- | -------- | -------: | ---------- | --- | --------: | -----------: |

Location may display both Area and Machine:

```text
Lathe / Lathe 3
```

When a Part Number is split across multiple locations, the board may show:

```text
Cut (4), Lathe 1 (2), Lathe 3 (4)
```

Sorting order:

1. Priority rank
2. Due Date ascending
3. Configured fallback sorting

Display behavior:

* Negative Days Left values appear in red.
* Priority work receives a strong but readable visual indicator.
* Location color may use the Area display color.
* Long lists rotate through pages automatically.
* Page size is calculated dynamically from available screen height.
* Rotation interval is configurable.
* The screen contains no normal navigation controls.

Excessive blinking should be avoided because it reduces readability on a continuously displayed board. A stable priority badge, accent, or limited pulse is preferable.

---

### 15.3 Area Board

The Area Board presents one column per Area.

Each column includes:

* Area name and description
* Total quantity
* Search
* Sort controls
* Active Part list
* Area and machine distribution
* Due Date or Days Left
* Priority indicator

Suggested item layout:

```text
[ICON] PF-BRACKET-00003        Qty 10
       PO-2026-000123          15 days left
       Cut (4), Lathe 3 (6)
```

Clicking Days Left may toggle between relative and absolute due date display.

Columns may scroll horizontally when they do not fit on the screen.

---

### 15.4 Tracking

The Tracking view is the primary Manager and Administrator workspace.

Capabilities:

* Search by Part Number
* Search by PO Number
* Search by Job Number
* Search by requester
* Filter by Area, Machine, Work Type, status, or due date
* Sort by any supported column
* Show or hide columns
* View active and completed quantities
* Print
* Export to PDF
* Export to spreadsheet formats

Selecting a Part Request or Work Batch opens its detail view.

---

### 15.5 Part Detail

Part Detail shows:

* Part master information
* Active Purchase Orders
* Internal requests
* Requested quantities
* Batch allocations
* Quantity by Area and Machine
* Current status
* Assigned route
* Route revisions
* Movement history
* Time spent at each route step
* Priority
* Due Date
* Requester
* Reason
* Job Numbers

A route visualization may display:

```text
Material: 0d
→ Cut: 1d
→ Lathe: 3d
→ Mill: 4d
→ External: 10d
→ Stockroom
```

Suggested visual states:

* Completed step
* Current step
* Future step
* Deviated or corrected step
* Overdue step

Authorized users may:

* Edit request information.
* Adjust quantity.
* Split or combine batches.
* Reassign allocations.
* Modify the route.
* Move quantity to another Area.
* Correct movement history through compensating events.
* Change priority.

---

### 15.6 Settings

Manager-visible settings may include:

* Production Board rotation interval
* Display columns
* Sorting defaults
* Confirmation behavior
* Area-specific scan options
* Worker session timeout
* Display preferences

Administrator-only settings may include:

* Departments
* Areas
* Machines
* Users
* Roles
* Permissions
* Barcode configuration
* Worker identification mode
* Route templates
* Offline behavior
* Quantity correction policies
* Audit policies
* Technical configuration

---

## 16. Completion Workflow

Stockroom is the final required Area.

When a Work Batch is scanned into Stockroom:

1. Record the Stockroom movement.
2. Mark the moved quantity as completed.
3. Apply the completed quantity to its Part Request allocations.
4. Check whether each affected Part Request is complete.
5. Check whether every Part Request in the Purchase Order is complete.
6. If all requests are complete, close the Purchase Order permanently.
7. Move the closed Purchase Order to History.
8. Return the reusable drawing folder to the Machine Shop office.

When the folder returns to the office:

* Staff check whether the Part Number has queued demand.
* If queued demand exists, a new Work Batch may be started.
* The folder is sent to the appropriate first Area.
* The new production movement begins when the folder is scanned there.

---

## 17. ERP Boundary

ERP integration must remain isolated from production tracking logic.

Rules:

* The MVP must work without ERP connectivity.
* ERP IDs must be stored separately from internal IDs.
* ERP Purchase Orders must be imported idempotently.
* ERP response formats must not leak into the domain model.
* Rework and Modification remain internal concepts.
* Internal generated request groups must never be uploaded as ERP Purchase Orders unless a future explicit integration requires it.
* Production movement history remains owned by CNC Part Flow.

---

## 18. Audit and Data Integrity

The system must preserve a complete audit trail for:

* Purchase Order creation
* Request creation
* Quantity changes
* Batch splits
* Batch merges
* Allocation changes
* Route assignment
* Route edits
* Priority changes
* Area movements
* Machine movements
* Worker sessions
* Undo operations
* Offline synchronization
* Administrative configuration changes

Tracking data must never be changed based on uncertain barcode resolution.

Database constraints and transactions should protect:

* Non-negative quantities
* Allocation totals
* Unique Job Numbers
* Unique event IDs
* Immutable completed Purchase Orders
* Valid Area and Machine relationships
* Idempotent offline event processing

---

## 19. Initial Scope

The first practical release should support:

* Machine Shop Department
* Part master records
* Manual Purchase Order entry
* File-based Purchase Order import
* Internal New, Rework, and Modification requests
* Areas and Machines
* Part Number barcodes
* Machine barcodes
* Optional Worker barcodes
* Linear routes
* Expected route-step duration
* Quantity splitting
* Quantity by Area and Machine
* Basic request-to-batch allocation
* Scan Station
* Production Board
* Area Board
* Tracking view
* Immutable movement history
* Undo through compensating events
* Priority ordering
* Stockroom completion
* Basic offline scan queue
* Role-based access

---

## 20. Deferred Capabilities

The following should remain outside the initial release unless proven necessary:

* Tracking every individual physical piece
* Barcode labels for every Purchase Order line
* Complex branching routes
* Full ERP synchronization
* Automated machine runtime collection
* Advanced production scheduling
* Inventory management outside tracked Part quantities
* Cost accounting
* Payroll or labor management
* General-purpose workflow engine
* Automatic resolution of conflicting offline movements

---

## 21. Guiding Principles

The application must be:

* Fast
* Clear
* Keyboard-first
* Scanner-first
* Tablet-friendly
* Auditable
* Reliable during production
* Safe under ambiguous input
* Practical for limited shop-floor staffing
* Expandable without becoming an ERP system

Operational convenience must not compromise quantity accuracy or movement history.
