# PartFlow Project Profile v21

> **Language:** English is the source of truth. [Tiếng Việt](./PROJECT_PROFILE.vi.md).

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

The PN string itself is the stable production identity. A PN:

- is unique in the ERP system,
- has no guaranteed format beyond the canonical form rules (case-insensitive, stored in canonical UPPERCASE, no whitespace — §7 Part Number),
- must otherwise be treated as an opaque arbitrary string,
- has one reusable drawing folder,
- has one unique PartFlow barcode,
- may be requested by multiple active Work Orders,
- may have multiple external Job Numbers (used only for display, searching, sorting, and reporting),
- may have quantities in multiple Areas simultaneously,
- may have quantities assigned to multiple Machines simultaneously,
- may have different quantity flows following different Routes,
- must remain permanently traceable after first recording: every production record (Work Order Demand, Quantity Flow, Part Movement, Allocation, history) keeps the canonical PN value it needs itself — traceability never depends on the existence of a PartNumber master record.

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

The PN is the primary tracked identity, and the **PN string itself is the stable domain identity** — there is no surrogate PN identity, and production data is never linked through a `part_number_id`.

A PN:

- is unique,
- remains the same across multiple Work Orders,
- is represented by the reusable folder barcode.

Canonical form rules:

- PN identity is **case-insensitive**. A PN is normalized to **UPPERCASE** before it is stored and before it is compared: `abc-123`, `ABC-123`, and `AbC-123` all canonicalize to `ABC-123`. The system stores, compares, and displays only the canonical uppercase PN.
- Normalization first **trims leading and trailing whitespace** (input chrome — spaces, tabs, newlines around the value). After trimming, the PN must be non-empty and must contain **no internal whitespace** of any kind (no space, tab, newline, or other whitespace inside the value). Input with internal whitespace is rejected as an invalid PN; internal whitespace is never silently removed to turn invalid input into a valid PN.
- Beyond these two rules the PN remains an opaque arbitrary string: it has no fixed format, PN segments are never parsed for business meaning, and no format is ever assumed.

`PartNumber` master data is **optional current metadata** for a PN (§8.1). Production identity and historical truth never depend on the existence of a PN master record.

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
- has a required **Asset Tag** — the stable, human-readable identity of the physical machine, assigned automatically by PartFlow when the Machine is created (§8.6),
- has its own barcode, which is the Asset Tag in the `PF:MACHINE:` namespace (`PF:MACHINE:<asset-tag>`, §10) — never an independent, manually entered value,
- becomes the current executor when quantity is assigned to it,
- also identifies the physical processing location while assigned.

A Machine has a lifecycle: it is active until it is **retired**. A Machine that was ever referenced by Movement history is never hard-deleted — it is retired instead, stays visible for historical display and reporting, never appears in assignment choices, and accepts no new scans.

A retired Machine may later **return to service** — but only as the **same physical machine** on the **same record**: reactivation keeps the stable identity, barcode, asset metadata, and complete history, and clears the retirement date. Retirement and reactivation are recorded as append-only lifecycle audit events (§8.6). A **different** physical machine is always a new record, never a reactivation.

A Machine keeps a stable internal identity; the operator-facing display name is separate and may be reused **across time and replacements** — but display names must be **unique among the active Machines of one Area**. Replacing a physical Machine means retiring the old record and creating a **new** record with its own stable identity and its own new Asset Tag (and therefore new barcode). The new Machine may reuse the familiar floor-position display name (for example `Lathe 1`); the old record is never renamed or mutated, and the two remain distinguishable through the Asset Tag and internal identity. There is no MachineSlot or WorkCenter abstraction — the display name alone carries the floor-position familiarity.

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

A Worker exists only within the Scan Station scope and is a different concept from a User: a **User** is an application account for Management, Administration and the other non-Scan-Station views; a **Worker** is the audit identity of the person operating a Scan Station. A Worker badge never logs into PartFlow, and a Worker Session is never an authentication session. Workers and Users are never merged into one entity.

---

# 8. Domain Model

## 8.1 PartNumber

Represents the **optional current metadata** for a PN. The canonical PN string itself is the stable production identity (§7 Part Number); the PartNumber master only enriches that identity with current metadata when the record exists.

```text
PN string        = stable production identity
PartNumber master = optional/current metadata for that PN
```

Typical attributes (illustrative only):

- `part_number` (the canonical uppercase PN — the identity and natural key)
- `name`
- `description`
- `image_url`
- `current_revision`
- `erp_id`
- `created_at`
- `updated_at`

Rules:

- `part_number` is stored in canonical form (§7 Part Number): normalized to UPPERCASE, containing no whitespace, otherwise an opaque arbitrary string. Uniqueness follows directly from the canonical form — one master record may exist per canonical PN.
- A PartNumber master is **created on first valid use**: no pre-populated authoritative PN catalog is required, ERP is never called during MVP, and manual PN entry accepts any PN value that is non-empty after trimming and free of internal whitespace (canonicalized to uppercase). The master record exists for current metadata and future ERP mapping only.
- Production identity and historical truth **never depend on the existence of the PartNumber master record**. WorkOrder demand, QuantityFlows, PartMovements, Allocations, and history each keep the canonical PN value they need themselves; historical display never requires a join through the PN master to know what the PN is. A PN master lookup only enriches current metadata when the record exists.
- Real PN values are commonly multi-segment hyphenated numeric strings of varying length (shapes such as `214-406`, `78-04-0031`, `0455-20-0118-03`, `2027-60-8114-00`). The canonical string must be preserved everywhere; PN segments must never be parsed for business meaning.
- `name`/`description` are free text as supplied — commonly uppercase with commas, slashes, fractions, dimensions, and manufacturing abbreviations (e.g. `VALVE, SOLENOID VITON, 3/8`) — and may be long enough to span multiple display lines.
- The folder barcode identifies only the PN and carries the PN itself: `PF:PN:<part-number>` with the canonical uppercase PN (§10).
- PartNumber master metadata is production master data maintained in **Management → Part Numbers** (§21) by authorized production roles (§20), like Machines and Planned Routes. An authorized user may **hard-delete** a PartNumber master record there (§28 Administrative Archival and Purge): deletion removes only the metadata, never cascades into production data, and never affects the PN's traceability — historical surfaces continue to display the PN string normally. If the PN is used again later, a new master record may be created for the same canonical PN identity. There is no PN archive or tombstone kept merely to preserve PN identity.
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
- `completed_at`
- `erp_id`
- `created_at`
- `updated_at`

Rules:

- `work_order_number` must be treated as an arbitrary string.
- `work_order_number` is **nullable** (§7 Work Order). A null number is valid data on an internal Work Order, displays as `—` (never persisted as a placeholder), and may be replaced by the real external number through an audited edit. The database allows multiple Work Orders with a null number while preserving uniqueness for non-null numbers (partial unique index).
- `received_date` is required and defaults to the current date during manual creation.
- `due_date` may be null. A missing Work Order due date is valid data, not a validation error; it may be added later. The Work Order due date serves as the entry default for demand-line due dates.
- A Work Order contains one or more Work Order Demand records.
- A Work Order is complete when every Work Order Demand has been fully allocated. Completion is **derived state**: the allocation records remain the source of truth.
- `completed_at` records the completion moment (the **done date**): it is set to the timestamp of the allocation event that fully allocated the last open Work Order Demand. It exists so the permanent completed history can be ordered, filtered and paged by done date efficiently (indexed; keyset pagination over `(completed_at, id)`) — it is never entered by hand.
- A later audited allocation adjustment (§18) may make a Work Order incomplete again: `completed_at` is then cleared (and set again by a later completing allocation). The Work Order returns to active views; nothing is deleted and the allocation audit trail preserves the full history.
- Completed Work Orders move out of active views but remain permanently available in history — presented on a dedicated read-only Completed Work Orders surface, ordered and default-filtered by done date and searchable by WO Number, PN and Job Number (§21 Work Orders). Because completed history is retained permanently, that surface must treat the list as unbounded: its search, filtering, ordering and paging are server-side.
- Work Order Number uniqueness (and the never-duplicate rule, §13) spans the whole history including completed Work Orders.

---

## 8.3 WorkOrderDemand

Represents how many physical pieces of a PN are requested by one Work Order.

Typical attributes (illustrative only):

- `id`
- `work_order_id`
- `part_number` (the canonical uppercase PN — kept by the demand itself)
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
- Once production quantity has been released for a Work Order Demand, only the restricted edit of §13 *Restricted edit after release* remains: requested quantity (never below the released or allocated quantity), due date, and Job Numbers. The line can no longer be removed (§13 *Work Order Demand Removal*).
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
- `asset_tag` (required; assigned automatically at creation, immutable)
- `barcode_value` (always equal to `asset_tag` — the barcode is derived, never entered)
- `description`
- `manufacturer`
- `model`
- `serial_number`
- `installed_on`
- `notes`
- `maintenance` (explicit override: started timestamp, optional note, optional expected return date)
- `state_changed_at`
- `retired_on`

The **Asset Tag** is required on every Machine: it is the stable, human-readable identity of the physical asset — it identifies the physical machine even when display names are reused across replacements — and it doubles as the Machine barcode (`PF:MACHINE:<asset-tag>`, §10). Asset metadata beyond it (manufacturer, model, serial number, installed date, notes) is optional — production tracking never depends on it.

Asset Tag rules:

- PartFlow assigns the Asset Tag automatically when a Machine is created; it is never entered or edited by hand.
- The format is configured in Administration → Barcode configuration and is deliberately simple: a prefix plus a zero-padded numeric sequence (for example prefix `CD-` with 4 digits → `CD-0001`, `CD-0002`, …). There is no generic formatting/template engine.
- Asset Tags are unique and are **never reused** — a retired Machine keeps its Asset Tag forever, and a replacement Machine always receives a new one.
- An Asset Tag never changes after the Machine is created. A format change applies to Machines created afterwards only; existing Asset Tags are never renamed or regenerated.
- The Asset Tag is the human-readable identity of the physical Machine — it is not the database-internal identity (`id`).

Rules:

- Every Machine barcode must be unique. The barcode is the Asset Tag in the `PF:MACHINE:` namespace — there is no independent Machine barcode identifier, and it is never entered manually.
- A Machine belongs to exactly one Area. The Area of an existing **active** Machine is fixed — moving production capacity to another Area is a replacement (retire + new record), never an edit that would make history ambiguous. The only exception is reactivation of the same physical machine that was physically moved while retired (below) — a forward-looking Area change that never touches history.
- Display names must be **unique among the active Machines of one Area**. Reuse across time and across replacements stays allowed — the uniqueness rule constrains only simultaneously active Machines of the same Area.
- Machine assignment identifies the current executor.
- An Area without Machines involves no Machine assignment at all.
- Assignment behavior follows from the Area's Machines (§12): with Machines, quantity queues and is assigned through the explicit one-shot workflow (UI label `Assign to Machine`) — never by configuration and never by Machine count.
- The operational state is **derived**, never chosen by users: (1) an explicit Maintenance override wins; (2) otherwise assigned active quantity means Running; (3) otherwise the Machine is Idle.
- A `state_changed_at` timestamp records when the operational state last changed; every surface derives the visible elapsed time in state from it (compact formats such as `18m`, `1h 24m`, `2d 03h`, `<1m`). No formatted duration is ever stored.
- Maintenance may start while quantity remains assigned. Starting maintenance never moves, releases, completes, or transfers quantity and never rewrites history; it may carry an optional note and an optional expected return date. While maintenance is active, the note and expected return date may be updated in place — without changing the maintenance start time, the state, or history. Clearing maintenance returns the Machine to Running when quantity is still assigned, otherwise to Idle.
- Retirement is blocked while active quantity is assigned — the quantity must first complete or transfer through the normal production workflow. A Machine ever referenced by Movement history is never hard-deleted; it is retired (operator wording: `Retired`, `Retire Machine`, `Retired on …`) and remains available for historical display and reporting only.
- A retired Machine never appears in assignment choices and accepts no new scans.
- A retired Machine may **return to service (reactivation: RETIRED → ACTIVE)** — for the **same physical machine only**, on the **same record**: identity, barcode, asset metadata, and history are unchanged, and `retired_on` clears. During reactivation a new active Area may be chosen when the physical machine moved while it was retired; the change is forward-looking only — historical Movements keep their recorded Areas. The reactivated Machine returns as Idle (the operational state stays derived — assigned quantity, not reactivation, makes it Running).
- Reactivation is blocked when the Asset Tag (which is also the barcode) or the serial number has meanwhile been reissued to another active Machine — with never-reused Asset Tags this indicates a data problem that must be resolved first — when the target Area is not active and no replacement Area is chosen, or when the display name would collide with an active Machine of the target Area.
- Retirement and reactivation are recorded as **append-only lifecycle audit events** (`RETIRED`, `REACTIVATED`): who, when, reason, the before/after state, and the previous and current Area when the Machine moved. (Phase 2 mocks these events; persistence arrives with the relevant backend phase.)
- Replacement follows §7 Machine: retire the old record, create a new record with its own stable identity and its own new Asset Tag (and barcode); the display name may be reused, the old record is never mutated. A **different physical machine is always a new record** — never a reactivation.

---

## 8.7 QuantityFlow

Represents an internal tracking concept for active PN quantity.

Typical attributes (illustrative only):

- `id`
- `part_number` (the canonical uppercase PN — kept by the flow itself)
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
- `part_number` (the canonical uppercase PN — kept by the Movement itself)
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
- A Movement keeps its own canonical PN value: history continues to identify the PN (for example `2027-60-8114-00`) even when no PartNumber master record exists (§8.1).
- Movement is not tied to a specific Work Order Allocation.
- Movement must never be silently overwritten.
- Corrections preserve the original event.
- Current state must be reconstructable from Movement history.

---

## 8.12 WorkOrderAllocation

Represents the assignment of stocked PN quantity to Work Order Demand.

Typical attributes (illustrative only):

- `id`
- `part_number` (the canonical uppercase PN — kept by the allocation itself)
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

Represents an operator (§7 Worker — Scan-Station-scoped audit identity, never an application account).

Attributes:

- `id` — stable internal identity
- `name`
- `badge_barcode` — the barcode already printed on the company's existing employee badge (§10); unique among Workers
- `avatar`
- `is_active`

Workers have **no employee number**.

Worker identification is configurable per Area.

Supported modes:

- disabled,
- fixed Worker,
- scanned Worker Session (§19 — sliding inactivity timeout).

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
PF:MACHINE:<asset-tag>
PF:AREA:<stable-id>
PF:SCRAP
```

The `PF:` namespace exists to identify PartFlow-owned barcodes, determine the entity type safely, and avoid confusing unrelated factory or vendor barcodes with PartFlow entities.

**Worker badges are the one deliberate exception to the `PF:` namespace (v18 — supersedes the former `PF:WORKER:<stable-id>` format, which no longer exists):** the Worker badge barcode is the barcode already printed on the company's existing employee badge — PartFlow never prints or requires a dedicated Worker barcode. A scanned value outside the `PF:` namespace is **exact-matched** against the `badge_barcode` values of **active** Workers (§8.13); on exactly one match it identifies that Worker, otherwise it is an unknown barcode and is rejected with nothing recorded — never guessed. Badge barcodes must stay unique among Workers, and an inactive Worker's badge matches nothing.

The Machine barcode carries the Machine's Asset Tag (§8.6) — the automatically assigned, immutable identity of the physical machine (`Asset Tag CD-0512` → scanned barcode `PF:MACHINE:CD-0512`). There is no independent Machine barcode identifier and no manual Machine barcode entry.

The PN barcode carries the PN itself, in canonical uppercase form (§7 Part Number). Parsing rules:

1. Trim scanner terminators and surrounding whitespace from the ends of the scanned value; nothing inside the value is ever removed.
2. Require the exact `PF:PN:` prefix for scanned PN barcodes.
3. Treat the entire suffix as the PN candidate and normalize it (§7 Part Number): surrounding whitespace is trimmed; after trimming the candidate must be non-empty and must contain no internal whitespace — a suffix with internal whitespace is an invalid PN barcode and is rejected, never silently cleaned up.
4. Canonicalize the candidate to UPPERCASE — the canonical PN (identity is case-insensitive, §7).
5. Never parse PN segments and never validate a PN format beyond the canonical form rules.
6. Do not require the PN to exist in a preloaded catalog — the PartNumber master record is created on first valid use (§8.1); ERP is not called during MVP.

There are **no Action barcodes**. Action intent (Modify intake, Repair, Scrap, quantity addition) is selected through explicit one-shot dialogs, never through persistent armed barcode state. `PF:SCRAP` is a single dedicated, context-sensitive barcode: it is accepted only inside the Scrap workflow, where each scan increments a pending scrap counter (§11); scanning it in the main Scan Station input is rejected.

Requirements:

- Barcode values must be unique.
- Barcode identity must not depend on mutable display names.
- Raw ERP PN text must not automatically be treated as a PartFlow barcode.
- Manual PN entry must remain available and accepts any PN value that is non-empty after trimming and free of internal whitespace; the entry (including lowercase input) is canonicalized to the uppercase PN before use.
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
4. Create the PN master metadata record if the PN is new (the barcode carries the canonical PN itself, §10).
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

The starting Area may be Material or another configured starting Area. A **terminal** Area is never a starting Area (v20): it is where finished quantity ends (§18), so production release into it is refused.

**Partial and repeated release (v20 — decided):** a Work Order Demand may be released in **parts**: releasing 20 of a 50-piece demand, then 12, then 18 is normal, and each part is its own explicit release creating its own Quantity Flow — parts are never merged, and every part after the first meets the existing-active-quantity confirmation rule stated next. The demand's released quantity is the sum of the release Movements that record it, and the remaining quantity (requested minus released) is a hard limit: releasing more than remains, or releasing at all once nothing remains, is refused and creates nothing.

**Restricted edit after release (v21 — decided; supersedes the v20 "a released demand line is read-only" rule):** business demand keeps changing after production has started — quantities grow, due dates move, Job Numbers arrive — so a Work Order Demand line that has released quantity stays **editable within limits** instead of freezing:

- **Requested quantity** may be edited, but never below what is already committed to the line: `max(released quantity, allocated quantity)`. Raising it is always valid — the line then has remaining quantity to release again, and its Work Order returns to `Open` accordingly (§8.2 status is derived from the demand lines). Lowering it to exactly the committed quantity is valid and simply leaves nothing to release.
- **Due date** and external **Job Numbers** may be edited like on any other demand line.
- **Part Number** stays uneditable — as on every saved demand line, a different PN is a new line, never a rewrite.
- **Request Type** is fixed once quantity has been released: what was released was released as `NEW` or `MODIFY` work, and that is history. **Requester**, **reason** and **notes** stay fixed with it; opening them is a separate decision, not a side effect of this rule.
- **Removal stays refused** (§13 *Work Order Demand Removal*), and no demand edit ever rewrites released quantity, Quantity Flows, Movement history, or release history — later production adjustments go through the correction and production workflows (§16).

The rule is enforced where the data is written, never only in the UI, and an edit of a released line serializes against a release of the same line so the released quantity can never exceed the requested quantity.

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

The operator-facing dialog is titled **Receive Quantity**, with **Confirm receipt** as the final confirming action; `intake` remains the internal workflow name (identifiers, comments) and never renders to operators.

A Work Order Demand counts as **active** here by its remaining business shortage: `requested_quantity > allocated_quantity`. Released quantity never decides it — a line released in full but not yet allocated is still active demand — and `completed_at` is the aggregate state of the whole Work Order (§8.2), never the state of one line: a fully allocated line is inactive even while its Work Order stays open because of its other lines. While any active demand of the PN exists, its quantity belongs to an explicit production release from Management (§13) and the Scan Station never receives it.

When a scan opens the intake flow (PN has no active Work Order Demand):

1. Scan or enter the PN (created on first valid use, §8.1).
2. Confirm or change the Request Type — `MODIFY` is the **default, not a forced value**.
3. Confirm or change the Route Mode — `FLOATING` is the **default, not a forced value**; a Planned Route is selected only when `PLANNED` is chosen.
4. Confirm quantity, optional due date (owned by the WorkOrderDemand — the PN never owns a due date), and reason/notes where applicable.
5. Confirm the starting/current Area and, when needed, the Operation. `received_date` defaults to the scan timestamp: the instant the PN scan opened the workflow, carried unchanged through every step of the wizard and read as a calendar date on the site's own calendar (`SITE_TIMEZONE`, §8.2). It is the SCAN that dates the receipt, never the confirmation — a receipt prepared before midnight and confirmed after it still belongs to the day it was scanned — and the scan instant is part of the confirmed intent, so a retry records the same date.

On confirmation, the transaction creates or reuses the PartNumber, creates or reuses an applicable internal blank-number MODIFY Work Order, creates the WorkOrderDemand and QuantityFlow, records the initial Movement, establishes the current position, and places quantity in the Area queue (Area with Machines) or directly into Area processing (Area without Machines).

Work Order reuse must never guess: if the same PN has exactly one clearly applicable active blank-number MODIFY Work Order — no external number, not completed, and already carrying a `MODIFY` demand line for that PN — reuse it; if multiple are plausible, an explicit selection is required and no first match is taken.

Reuse keeps the one-canonical-PN-per-Work-Order rule intact: the reused Work Order's existing demand line for the PN is **raised** by the received quantity — the restricted edit of §13, where raising a released line's requested quantity is always valid — instead of the Work Order gaining a second line for the same PN, and the receipt releases exactly that increment. Only what restricted edit permits is touched: the requested quantity and, when the operator entered one, the due date. An existing line's Request Type, requester, reason and notes are never rewritten by a station receipt — the receipt's own reason travels on its immutable Movement. A `NEW` receipt never reuses (reuse is defined for blank-number MODIFY Work Orders only) and always creates its own internal Work Order.

A confirmed receipt is not undone at the Scan Station: the reversal of §16 restores production state and never rewrites the business demand a receipt created or raised, so a mistaken receipt is corrected through the production correction workflows rather than half-reversed.

If the PN already has active quantity, the system must explicitly confirm whether the new quantity joins an existing Quantity Flow or creates a separate one. The system must never infer this from PN identity alone.

What "joins an existing Quantity Flow" *means* for a receipt is a **remaining open decision** (§32 decision 3) and is deliberately not defined here: which of several active flows may be joined, what happens to their route mode, Assigned Route and position, and which Movement records the join. Until it is decided, no workflow may guess one: the Scan Station `Receive Quantity` of this section exists only where the PN has NO active quantity, and a receipt whose PN gained active quantity between the scan and the confirmation is refused with nothing recorded rather than resolved by inference. Quantity found beside quantity already active in the Area stays the `QUANTITY_ADJUSTED · INCREASE` correction of §11, which joins nothing.

## Repair

`REPAIR` means some or all quantity already in production must return to a previously visited Area to correct work performed earlier:

```text
A → B → C → D → B
```

Rules:

- Repair does not create new physical quantity, is not a WorkOrderDemand, and is not a Request Type.
- Repair operates on an existing QuantityFlow: partial Repair splits the flow (depends on SPLIT); full-flow Repair moves the whole flow.
- Repair is recorded as a movement intent: `movement_type = TRANSFERRED`, `movement_reason = REPAIR` (§8.11) — no separate Repair aggregate.
- Returning to a previously visited Area must **never** automatically be assumed to be Repair — a previously visited Area may legitimately be the next normal production step, and a normal transfer there remains possible. The user explicitly chooses the Repair intent (UI wording: “Return quantity for repair”, never “Create REPAIR demand”).
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

The Worker recorded on the reversal follows the Area's Worker ID mode (§19): disabled records no Worker; fixed Worker records the configured Worker; a valid scanned Worker Session records the **Worker active at the moment the Undo is confirmed**. **Final confirmation (v19 — supersedes the v18 "no extra badge scan" rule):** Undo carries the shared final-confirmation gate of the sensitive actions (§19) — in a scanned-session Area with the `UNDO` badge-confirmation option enabled (the default), a Worker badge scan after the reversal summary completes the reversal and identifies the confirming Worker (any active badge; the scan also signs that Worker in); in every other case — a fixed-Worker or disabled Area, or the option disabled — a final warning-toned confirmation question restates the key facts before anything is reversed; the final gate itself is never skipped. In scanned-session mode with no active or an expired session, the Scan Station is already blocked until a valid badge scan (§19) — so Undo is only reachable with a valid session.

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

- scanning a Worker badge activates that Worker,
- subsequent scans use the active Worker,
- a Worker scan never replaces the last-scanned-PN context,
- scanning a different active Worker's badge switches the active Worker immediately — no sign-out step,
- the session ends when another Worker signs in, the Worker signs out, or the session expires.

**Expiration (v18 — the rules are decided):** a scanned Worker Session expires through a **sliding inactivity timeout**, never at a shift boundary — there is no shift-end concept and no shift-schedule management:

- the timeout value is configured in Administration → Worker sessions: one default policy value with optional per-Area overrides; it is never inferred from the number of Workers in an Area;
- every valid production interaction at the Scan Station refreshes the timeout — a successfully resolved scan, a confirmed production command, a valid badge scan; invalid or unknown scans never refresh it;
- on expiration **only the Scan Station is blocked**: it shows a blocking badge-scan modal (`Worker session expired` / `Scan your badge to continue.`); every other application view is unaffected;
- a Scan Station in scanned-session mode opened without an active session shows the same blocking badge-scan requirement before production interaction;
- if a production dialog is open at expiration, its draft and selections are preserved, but production confirmation stays blocked until a valid badge scan; afterwards the workflow continues where it was.

**Badge confirmation of sensitive actions (v19):** each of the three sensitive Scan Station actions — `DONE` (Area completion), `QUEUE` (return unfinished quantity to queue) and Undo — carries its own **badge-confirmation option** in the Worker sessions policy (Administration → Worker sessions), default **enabled**. Every sensitive action ALWAYS ends in a final confirmation gate; the option decides only whether that gate must be a Worker badge scan:

- in a **scanned-session** Area with the option enabled, the action requires a Worker badge scan as the **final step after its confirmation summary** — the key facts of the pending action stay visible, any ACTIVE Worker badge confirms, the badge identifies the confirming Worker (recorded on the action, and signed in exactly like a normal badge scan — switching and refreshing the session), unknown or inactive badges are rejected with nothing recorded, and cancelling the gate returns to the summary;
- in a **fixed-Worker or disabled-Worker** Area — and in a scanned-session Area whose option is disabled — the gate is a **final confirmation question** restating the key facts (no badge is required) — an information-toned presentation for DONE, a warning presentation for QUEUE return and Undo;
- the gate itself is never optional: no configuration removes the final step;
- nothing is ever recorded before the gate completes — the gate is part of the confirmation, never a separate write.

Worker identity is accountability metadata and must never determine business correctness.

There is **no Machine session** of any kind: Machine selection is always part of a one-shot assignment action (§12, §15). The active Worker must always be visible on the Scan Station.

Session state reduces repetitive scanning but must never replace persistent Movement history.

---

# 20. Roles and Permissions

PartFlow uses role-based authorization.

Machines, Route Templates (Planned Routes), and PartNumber master metadata are production master data — operational management functions, not system administration. Managing them is **permission-based**: an authorized production specialist — for example a Production Manager, Process Engineer, or Maintenance Manager — may manage Machines, Route Templates, and PartNumber master metadata without being an Administrator. Administrators retain these capabilities, but they are not Administrator-exclusive, and Administration keeps no duplicate Machines, Route Templates, or Part Numbers screens (§21).

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
- manage PartNumber master metadata, including hard deletion (shared with authorized production roles — managed through Management → Part Numbers, §21),
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
- visible active Worker per the Area's Worker ID mode (§19): the scanned session shows the active Worker with a live remaining-session countdown, a fixed-Worker Area its configured Worker, and a disabled Area shows no Worker element at all (Worker identity does not exist there; the mode stays visible in Administration); a scanned-session Area without an active session — on open or after expiration — blocks only the Scan Station behind a badge-scan modal, preserving any open dialog's draft while blocking production confirmation,
- visible last scanned PN inside the scan card, with the Undo action anchored at its right edge (only completed PN operations become the last scanned PN; Worker scans and cancelled dialogs never replace it),
- quantity entry only when required,
- current Area quantity in the shared Area/Machine monitoring layout (§ Area Board) — the `In this Area now` card left, Machine cards in a right-side grid that wraps within itself; no Machine region for Areas without Machines; Machine cards show the derived operational state with its elapsed time in state (`running · 1h 24m`, derived from the state-change timestamp — §8.6) and, under maintenance, the maintenance note and expected return date,
- separate on-Machine, queued, and finished (`Finished — ready to move`) quantity (Areas with Machines) or direct processing and finished groups (Areas without Machines) — finished quantity belongs to the Area summary and Machine cards show only actively assigned quantity,
- Machine-card PN rows with the two distinct actions `DONE` and `QUEUE` (§12),
- authorized Undo with a summary confirmation (§16),
- manual entry fallback accepting any PN value that is non-empty after trimming and free of internal whitespace (canonicalized to the uppercase PN, §7 Part Number).

There is no persistent Machine Session, no pending armed context, and no Recent Scans list.

---

## Production Board

The Production Board is a read-only large-screen Department display. It is Department-wide only: there is **no per-Area filtered mode** (v18 — decided) — per-Area monitoring is the Area Board's responsibility, and the two views never duplicate each other.

Requirements:

- readable from a distance,
- automatic pagination,
- automatic page rotation with a dwell time **proportional to the rows displayed on the current page** (default 3 seconds per displayed row, with a 6-second minimum page dwell); the rotation countdown indicator uses the same deadline and the same per-page duration; the values are configuration **per Department** (future Administration → Department display settings, §22 — never global, never hard-coded UI constants),
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

Area Board is one Management view with two modes behind a single tab strip: the **All Areas overview** and the **per-Area detail** (GUI_DESIGN §6). The "Manager Summary" name is retired — its content is the All Areas overview below; no content was dropped, only its placement changed.

### All Areas overview

The All Areas overview provides an operational overview grouped by Area.

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

### Per-Area detail

The per-Area detail provides a focused view of production currently owned by one Area. It uses the same shared Area/Machine monitoring layout and presentation components as the Scan Station (`In this Area now` summary card left, Machine monitoring cards in the right-side grid; the summary card spans the full width for Areas without Machines) — read-only, without the Scan Station action buttons, and without visual drift between the two views.

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

## Machines

Machines is the management view for Machine lifecycle, maintenance, and asset identification — production master data managed by authorized production roles (§20), grouped under Management alongside Area Board, Tracking, Work Orders, Planned Routes, Part Numbers, and Priority. Running and idle are derived from assigned quantity; maintenance is the only state set by hand (§8.6).

It must provide:

- a table of active Machines with sortable columns: identity (display name and Area), derived operational state with the elapsed time in state, currently assigned PN portions with quantities, asset metadata (Asset Tag, manufacturer, model), and a per-row **Maintenance On/Off switch** that opens the existing start-maintenance (optional note, optional expected return date) and clear-maintenance dialogs — there is no separate actions column,
- in-place editing of the maintenance note and expected return date from the Edit Machine dialog while a Machine is under maintenance (§8.6) — never a state change,
- whole-row activation: selecting a Machine row opens the Edit Machine dialog,
- a separate Retired Machines table with sortable data columns: name, `Retired on YYYY-MM-DD`, asset metadata, notes — historical display and reporting with no action column; whole-row activation opens the read-only Retired Machine Details dialog, and **Reactivate** (§8.6 return to service) lives only inside that dialog; retired Machines still never appear in assignment choices and accept no new scans,
- a New Machine / Edit Machine dialog: display name (required), Area (selectable for a new Machine, fixed for an existing one — §8.6), the read-only Machine identity — the Asset Tag assigned automatically at creation per the configured format (§8.6) and the barcode derived from it (`PF:MACHINE:<asset-tag>`, §10; neither is ever entered or edited) — optional manufacturer, model, serial number, installed date, and notes, and the Machine's append-only lifecycle audit events (`RETIRED` / `REACTIVATED` — §8.6),
- a printable Machine barcode label from the Edit Machine dialog: the display name, the Asset Tag, and the `PF:MACHINE:<asset-tag>` barcode,
- staged creation: a new Machine is added only after a summary and a final explicit confirmation stating that a Machine record cannot be deleted — only retired,
- retirement as the Edit dialog's **Danger Zone** action: `Retire…` requires a typed identifier confirmation — always the Asset Tag, never the reusable display name — after an explicit consequences warning, and a final explicit confirmation that the retirement is recorded permanently in the Machine lifecycle,
- retirement blocked while active quantity is assigned, with the quantity completing or transferring through the normal production workflow first,
- reactivation of a retired Machine per §8.6: same physical machine on the same record, required reason, an optional new active Area applied forward only, blocked on reissued identity (Asset Tag — which is also the barcode — or serial number) and on active-name collisions in the target Area, returning the Machine as Idle after a final explicit confirmation that the reactivation is recorded permanently in the Machine lifecycle,
- replacement guidance following §7 Machine: retire the old record, create a new record with its own identity and its own new automatically assigned Asset Tag (and barcode); the display name may be reused (unique among active Machines of one Area).

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
- normal PN display for PNs whose master metadata record was deleted: the canonical PN string and all production history remain fully visible; only the master-derived metadata (name, image, revision, ERP mapping) is absent (§8.1),
- a blank external Work Order Number as `—`.

Route visualization supports both modes. For Planned Routes it must distinguish completed, active, queued, and future steps plus deviations. For Floating Routes it derives the actual trace from Movement history, shows repeated Areas, shows split flows independently, and marks Repair transfers explicitly, for example:

```text
A → B → C → D → B
                ⟲ REPAIR
```

It must not imply that the entire PN is at one Route Step.

---

## Work Orders

Work Orders is the management view for manual Work Order entry (the Work Order Intake workflow, §12). It is a light-theme management view. The Work Order list spans the full view width, its search and the New Work Order action share one toolbar row, and selecting a Work Order row opens the Work Order Details dialog.

The view must support the minimum confirmed workflow:

1. Create or locate a Work Order.
2. Add or update one or more Work Order Demand records.
3. Locate or create the PartNumber.
4. Create the PN barcode when the PN is new.
5. Enter: Work Order Number (optional — a blank number is saved as NULL on an internal Work Order and displays as `—`, §7 Work Order), received date (defaults to the current date), PN (any PN text that is non-empty after trimming and free of internal whitespace, canonicalized to uppercase — created on first use, §8.1), Request Type (default `NEW`), requested quantity, due date (optional — a missing due date is valid data, §8.3), priority when applicable, external Job Numbers, and requester, reason, and notes when applicable.
6. Save business demand without automatically creating production quantity.
7. Provide a separate explicit `Release to production` action following the release steps in §12.

On production release the view must confirm release quantity, Route Mode (Floating by default; a Route only for Planned), and the configured starting Area and Operation, and show the resulting Quantity Flow, route mode, Area, quantity, and `RECEIVED` Movement.

Completed Work Orders (every Work Order Demand fully allocated, §8.2) never appear in the active list. They live on a dedicated read-only **Completed Work Orders** history page reached from the list: ordered and default-filtered by done date (`completed_at`, newest first, a bounded default range), with its own search (WO Number, PN, Job Number), a done-date range filter, a due-outcome filter and incremental paging. The history is retained permanently and unbounded, so its search, filtering, ordering and paging are server-side; the page offers no entry, editing or release actions. The active list's search miss points at the completed history, and entering a completed Work Order's number in New Work Order announces the completion and opens its read-only details instead of duplicating (Work Order Number uniqueness spans the whole history, §8.2).

If the PN already has active quantity, the view must show the existing distribution and require explicit confirmation of intent; it must never automatically create additional physical quantity or merge Quantity Flows.

Work Order Intake must not grow into ERP-style customer, pricing, invoicing, shipping, purchasing, or accounting functionality.

---

## Planned Routes

Planned Routes is the management view for reusable route definitions (`RouteTemplate`, §8.8) — production master data managed by authorized production roles (§20) within the Management grouping. The name keeps templates clearly apart from the Floating actual route traces shown in Tracking.

It must provide:

- a searchable list of active route templates — name and description, the compact ordered Area step sequence presented as Area-colored chips, status (Active with its updated date), and usage — plus a separate table of archived templates (with their archived dates),
- usage per template: the Quantity Flows released with it (flow id, PN, released date), or `Never used`,
- whole-row activation: selecting an active template row opens the create/edit dialog; duplicate, and archive (ever-used) or delete (never-used only), live inside that dialog — archived templates offer only duplicate from their table,
- a create/edit dialog: name, description, and ordered steps (Area, Operation, advisory expected duration, optional preferred Machine, optional instructions) with explicit reordering (drag-and-drop plus Up/Down controls) and step add/remove; the Operation select is scoped to the chosen Area's Operations, and the preferred Machine select offers the Area's active Machines referenced by stable Machine id (`preferred_machine_id`, §8.9) — never by the reusable display name,
- archiving an ever-used template guarded by a typed confirmation (the exact route name) after an explicit consequences warning; deleting a never-used template stays a plain confirmation,
- an explicit note when editing a used template: changes apply to future assignments only; already released Quantity Flows keep their Assigned Route snapshots, and an in-production Assigned Route is changed separately in its own audited workflow with a reason (Tracking → Edit assigned Route),
- archived templates visible in historical context but never offered for new route assignments.

There is no separate template-versioning system — Assigned Route snapshots preserve historical definitions, and Planned Routes guide production without ever replacing or rewriting actual Movement history.

---

## Part Numbers

Part Numbers is the management view for PartNumber master metadata (§8.1) — production master data managed by authorized production roles (§20) within the Management grouping. The canonical PN string itself remains the stable production identity: the view maintains the optional metadata records that enrich a PN (name/description, image, informational revision, ERP mapping) and never gates production use — a canonical PN stays usable with or without a master record.

It must provide:

- a searchable list of PartNumber master records (PN, name/description, revision, ERP id) showing per record: the PN image (the one shared default PN image placeholder when no custom image was uploaded), the canonical uppercase PN, name/description, informational revision, ERP id, and the derived PN barcode value (`PF:PN:<part-number>`, §10) — the barcode is derived from the canonical PN and is never an independently editable value,
- whole-row activation: selecting a record opens the metadata edit dialog,
- creating a master record ahead of first production use: the entered PN is canonicalized (trimmed, internal whitespace rejected, uppercased — §7 Part Number), and one master record may exist per canonical PN; create-on-first-use at intake (§8.1) is unaffected,
- editing the metadata: name/description, informational revision, ERP id, and uploading, changing, or removing the PN image (removing returns to the shared default placeholder),
- viewing and printing a simple PN barcode label: the `PF:PN:<part-number>` barcode with the PN text beneath it — no additional barcode configuration,
- hard deletion of a master record per §8.1/§28: deletion removes only the metadata, never cascades into WorkOrderDemand, QuantityFlow, PartMovement, Allocation, or history, every surface keeps displaying the canonical PN normally, and a record may be created again later for the same canonical PN. There is no PN archive, no soft-delete, and no active/inactive lifecycle.

---

## Priority Management

Priority belongs to Work Order Demand.

The Hot list is managed within the Department:

1. Show Hot Work Order Demand sorted by explicit priority rank.
2. Add Work Order Demand to the Hot list by searching and selecting, or by scanning the PN barcode. A scanned PN barcode resolves deterministically (v18): no eligible Work Order Demand adds nothing; exactly one is added directly; more than one filters the candidate list to that PN and requires an explicit selection of the specific Work Order Demand — never a first-match guess.
3. If a PN has multiple active Work Order Demand records, each Work Order Demand is selected and ranked separately.
4. Add new Hot entries at the bottom by default; adding at the bottom applies directly.
5. Allow drag-and-drop reordering.
6. Allow removing an entry from the Hot list only after an explicit confirmation that identifies the PN and Work Order Demand; cancelling changes nothing. After confirmation the remaining ranks close the gap.
7. Require explicit confirmation before applying any operation that changes the order of existing Hot entries — drag-and-drop, Move Up, Move Down, Undo, and Redo. The confirmation identifies the affected PN and Work Order Demand, the previous rank, the proposed new rank, and the action type; cancelling leaves the list and both histories unchanged, and the visible list is never renumbered before confirmation.
8. Apply every confirmed Hot list change and record it in the audit trail.
9. Provide Undo and Redo for Hot list changes instead of a separate save-or-cancel step; stepping back or forward is itself an order change and requires the same confirmation. Undo/Redo depth is unlimited within the current application session (v18) — no numeric limit; the history ends with the session.
10. Use the stored rank as the highest work and allocation priority.

Multiple Work Orders requesting the same PN may have different priorities.

---

## Administration

Administration stays focused on system administration:

- Departments,
- Areas,
- Operations,
- Scan Stations,
- Workers (profiles — name, badge barcode, avatar, active status; §8.13 — kept separate from user accounts, never merged),
- users,
- roles,
- permissions,
- barcode configuration (the `PF:` prefix scheme and the Machine Asset Tag format — prefix + zero-padded numeric sequence, §8.6, §10),
- scan behavior,
- Worker session policies (the scanned-session sliding inactivity timeout — one default value with per-Area overrides, §19),
- Department display settings (per Department — including the Production Board rotation timing, §21),
- history archival and purge maintenance with retention settings (§28 Administrative Archival and Purge),
- application settings.

Machines, Route Templates, and PartNumber master metadata are not Administration screens: they are production master data managed permission-based in Management → Machines, Management → Planned Routes, and Management → Part Numbers (§20) with no duplicate Administration screens. Machines remain part of the minimum environment setup prerequisite — configured through Management → Machines before real production runs.

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

Normal production runtime is append-only and never silently rewrites or deletes history. Separately, Administrators hold **explicit maintenance authority in every environment** (**explicit Admin archival/purge maintenance**).

**PartNumber master deletion.** The PartNumber master record is optional current metadata (§8.1); every production record keeps its own canonical PN value. An authorized user may therefore **hard-delete** a PN master record through Management → Part Numbers (§20, §21) — for example a junk or test entry, or metadata that is simply no longer wanted — without any loss of production or history identity:

- deleting the PN master must not delete or mutate WorkOrderDemand, QuantityFlow, PartMovement, Allocation, or any history;
- the deletion never cascades into production data;
- historical surfaces continue to display the canonical PN string normally — only master-derived metadata (name, image, revision, ERP mapping) becomes unavailable;
- the deleted PN disappears from metadata-backed active lookup; intake of the PN remains possible (create-on-first-use, §8.1);
- if the PN appears again later, a new master metadata record may be created for the same canonical PN identity;
- there is no PN archive, soft-delete, or tombstone kept merely to preserve PN identity — the canonical PN string itself fulfills that responsibility.

**Movement history retention, archival, and purge.** PartMovement is immutable during active production runtime: normal workflows never modify or delete Movement records. Immutability does **not** mean that every Movement row must live in the primary PostgreSQL database forever — archival and purge under an explicit retention policy is lifecycle/storage maintenance, separate from production runtime. As Movement history grows, PartFlow may apply a **configurable retention policy** (for example: keep N years of history in the primary database — the retention period is configuration, never a hard-coded number of years in domain rules). Older history is then:

1. selected by the retention cutoff;
2. exported losslessly to archival files;
3. verified as successfully exported;
4. only then purged (deleted) from the primary database.

The archival files become the long-term historical storage for the purged portion of history. Mandatory rules:

- **Normal production runtime may never UPDATE or DELETE `PartMovement` rows** — the append-only guard (UPDATE/DELETE revoked from the application role plus a raise-on-write trigger) stays in force at all times for the application. Archive/purge is executed only through a **separate privileged Admin maintenance path** with its own authorization — never through the application role, the normal application workflows, or any production write path.
- **Never purge before a successful, verified archive export.**
- The archive must retain enough data to identify the PN, quantity, Movement type, Area, Operation, Machine, Worker, timestamps, relationships/correction context, and the required audit fields.
- The PN in archived history is stored directly as the canonical PN string — never dependent on the PN master.
- **Purge must never break relationships between Movement records.** Related Movements — for example a `REVERSED` correction and the original Movement it references through `reverses_movement_id`, or the Movements of one atomic application command — are archived and purged together: a retained Movement may never be left referencing a purged row. The retention cutoff selection must keep such chains whole (retain the whole chain until every member falls behind the cutoff), and the archive preserves the relationship context.
- Retention/purge is explicit administrative maintenance, never part of a normal production workflow; it may be triggered by the Admin-configured retention policy, an Admin-configured data-size threshold, or a manual Admin request.
- Maintenance operations require explicit Admin authorization, show a scope/impact preview before execution, require a reason, and are fully auditable: scope, cutoff, who/what initiated it, when, and the result.
- Records still needed to reconstruct active production state are never archived/purged; active production state and current projections must always remain reconcilable after archival.
- The purge deletes exactly the archived rows (archive/export first, then purge what was archived) — whole-table truncation is never used.
- Retention settings live in Administration/configuration, not in production workflow logic.
- The archive file format and transport are implementation-phase decisions; no archival engine is implemented before the roadmap phase that builds it, and no additional storage infrastructure is presumed here.

Note the distinction: *production identity must remain traceable* (always true — production records carry their canonical PN, and archived history remains part of the permanent record) is **not** the same as *all raw Movement rows must remain forever in the primary database* (not required — retention archival applies).

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
- Area Board (All Areas overview + per-Area detail),
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
2. Whether offline scan synchronization will be included in a later release.
3. What a `MODIFY` intake's explicit "join an existing Quantity Flow" choice does (§14): which active flow of the PN may be joined when several exist, what the joined quantity inherits (route mode, Assigned Route, current position, processing state), and which Movement records the join. Until this is decided, receiving quantity for a PN that already has active quantity is refused, never inferred.

(The former scrap question is resolved: `SCRAPPED` is a first-class Movement type, §8.11/§11. Machine sessions no longer exist, so no expiration rule applies to them. The former Worker-session expiration question is resolved in v18: scanned Worker Sessions expire through a configurable sliding inactivity timeout — Administration default with per-Area overrides, §19.)

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
