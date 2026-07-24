# PartFlow Implementation Roadmap

> **Authority:** Canonical for implementation order, phase boundaries, dependencies, and temporary limitations.
> Domain behavior and product scope are defined by [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md); the approved target UI by [`GUI_DESIGN.md`](./GUI_DESIGN.md).

## Current State

- Canonical project specification: `PROJECT_PROFILE.md` v6.
- Approved target UI: `GUI_DESIGN.md` v5, with mockup v5 (`docs/mockups/partflow-gui-mockup-v5.html`) as its visual reference.
- Phase 1 — Repository Foundation source code exists: React + TypeScript frontend, FastAPI backend, PostgreSQL, Alembic (no-op baseline), Docker Compose, the `/api/health` endpoint, formatter/linter/type-check/test tooling, and CI.

## Implementation Principles

- Build in vertical slices; complete one workflow end to end before expanding.
- Never transfer quantity before the system has a real quantity-introduction workflow: PO Intake and production release precede all real transfer workflows.
- Preserve Presentation → Application → Domain → Infrastructure.
- Use mock data only until the relevant backend slice exists; mock behavior never leaks into production.
- Ambiguity always requires explicit confirmation before any write; unknown or invalid input is rejected with no write.
- Production writes are blocked while disconnected; offline scan synchronization is deferred and unapproved.
- Do not implement ERP integration during MVP.

## Phase 1 — Repository Foundation

Scope:

- React + TypeScript frontend
- FastAPI backend
- PostgreSQL
- Alembic
- Docker Compose
- health endpoint
- frontend/backend/database connectivity
- formatter, linter, type checks, and test foundations

Completion criteria:

- Development environment starts from documented commands.
- Frontend calls the backend health endpoint; backend connects to PostgreSQL.
- Formatting, linting, type checks, and initial tests run successfully.

## Phase 2 — Frontend Design System and Application Shell

Scope:

- shared tokens
- dark and light contexts
- application routing/navigation
- approved mock views, including the Purchase Orders view in the application shell
- development-only mock data
- loading, empty, error, connectivity-loss, and long-data states

Non-goals:

- production business rules in mock components
- database writes
- ERP integration

## Phase 3 — Minimum Canonical Domain and Data Foundation

Only the foundation required by manual PO Intake and production release:

- Department
- Area
- Operation
- PartNumber
- PurchaseOrder
- PoDemand
- RouteTemplate
- RouteStep
- AssignedRoute
- QuantityFlow
- PartMovement
- derived current-position projection

Rules:

- Preserve canonical PartMovement field names when those fields are introduced: `station_id`, `occurred_at`, `server_received_at`, `device_event_id`.
- Do not introduce competing names such as `client_event_id`.
- Movement history is immutable; quantity integrity is enforced; domain invariants are covered by tests.

## Phase 4 — Manual PO Intake and Production Release

The first business vertical slice, presented in the UI as the Purchase Orders view (GUI_DESIGN §11). It must:

- create/find PurchaseOrder,
- create/find PartNumber,
- create/update PoDemand,
- save demand separately from production quantity,
- explicitly release production,
- create QuantityFlow,
- assign an independent Route snapshot,
- append `RECEIVED`,
- establish current position,
- remain transactional and idempotent,
- not automatically merge with existing active quantity.

Manual entry comes before file import. Development seed data may support UI development and tests, but seeded `RECEIVED` fixtures are not the product intake workflow.

## Phase 5 — Scan Station Transfer to an Area Queue

Pilot example: `Material -> Lathe queue`, where Lathe is configured as `Queue -> select Machine by scan`.

The slice must include:

- stable Scan Station configuration bound to Lathe,
- PN barcode resolution,
- source QuantityFlow resolution,
- Operation resolution,
- route validation,
- quantity validation,
- explicit ambiguity confirmation,
- immutable `TRANSFERRED` Movement,
- transactional current-position projection update,
- idempotent retry handling,
- keyboard focus restoration,
- recent scans and Area inventory refresh.

Candidate resolution must respect current position, route, Operation, station context, and valid deviations — never "every active flow outside the target Area".

Temporary limitation: full-QuantityFlow movement only. Partial movement must not be claimed as supported before SPLIT (Phase 8); partial input is refused clearly with no write. The target GUI requirement for partial quantity (GUI_DESIGN §4.7) remains.

## Phase 6 — Machine Assignment and Machine Sessions

- Machine barcode resolution
- Machine/Area validation
- PN -> Machine and Machine -> PN scan order
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE`
- sticky Machine session
- inactive Machine rejection
- direct single-Machine mode later in this phase or a clearly named sub-phase

## Phase 7 — Additional Area Ownership Modes

- Area without Machines takes direct processing ownership
- Operation recorded and Machine null
- direct single-Machine auto-assignment
- multiple-Operation confirmation
- behavior driven by Area configuration, never Machine count alone

## Phase 8 — Quantity SPLIT and MERGED Workflows

- partial movement
- preserved lineage
- quantity conservation
- `SPLIT` and `MERGED` Movement history
- quantity keypad supports valid partial amounts

## Phase 9 — Undo and Corrections

- `REVERSED` Movement
- original Movement preserved
- authorization
- reason when configured
- projection restoration
- audit visibility

## Phase 10 — Stockroom and PoAllocation

- `STOCKED` Movement
- available stocked quantity
- suggested allocation order:
  1. highest manager-defined PO Demand priority
  2. earliest due date
- equal criteria resolved by a stable deterministic tie-breaker (implementation detail, not a business rule)
- PoAllocation recorded separately from PartMovement
- authorization for allocation adjustment

## Phase 11 — Read Models and Monitoring Views

- Production Board
- Area Board, including the Manager Summary content in its All Areas overview (no separate Manager Summary view)
- Tracking
- movement-derived projections
- stale-feed and long-data states

## Phase 12 — Priority Management

- Hot PoDemand ranking
- add/search/scan
- reorder
- confirmation before removal
- immediate audited application
- Undo/Redo

## Phase 13 — Administration

- Departments
- Areas
- Operations
- Machines
- Workers
- Route Templates
- barcode configuration
- session policies
- correction permissions

## Phase 14 — Authentication and Role Enforcement

Role-based authorization per PROJECT_PROFILE §19.

## Phase 15 — File-Based PO Import

- idempotent import
- row validation
- clear partial-failure reporting
- no ERP dependency

## Phase 16 — Deployment and Production Hardening

- backups
- migrations
- HTTPS/internal access
- observability
- rollback
- reconciliation checks
- pilot deployment

## Deferred

- ERP synchronization
- offline scan synchronization (not part of MVP; production writes stay blocked while disconnected)
- advanced analytics
- speculative automation
- broad ERP/MES features
