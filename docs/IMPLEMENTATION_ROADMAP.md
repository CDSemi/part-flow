# PartFlow Implementation Roadmap

## Current State

- Canonical project specification completed.
- UI mockup v2 completed.
- No production source code exists yet.

## Implementation Principles

- Build in vertical slices.
- Complete one workflow end to end before expanding.
- Preserve Presentation → Application → Domain → Infrastructure.
- Use mock data only until the relevant backend slice exists.
- Do not implement ERP integration or offline synchronization during MVP.

## Phase 1 — Repository Foundation

Goal:
Create the executable project foundation.

Scope:
- React + TypeScript frontend
- FastAPI backend
- PostgreSQL
- Docker Compose
- Alembic
- Health endpoint
- Frontend/backend/database connectivity

Completion criteria:
- Development environment starts from documented commands.
- Frontend can call the backend health endpoint.
- Backend can connect to PostgreSQL.
- Formatting, linting, and initial tests run successfully.

## Phase 2 — Frontend Shell

Goal:
Convert the approved mockup into maintainable React screens.

Scope:
- Application layout
- Navigation
- Scan Station screen
- Realistic mock data
- Loading, empty, and error states

Non-goals:
- Production business logic
- Database writes
- ERP integration

## Phase 3 — Domain Foundation

Goal:
Implement the minimum domain required by the first workflow.

Scope:
- Job
- Part
- Production quantity tracking
- Area
- Operation
- Machine
- PartMovement
- Current derived state

Completion criteria:
- Domain invariants are covered by tests.
- Movement history is immutable.
- Quantity integrity is enforced.

## Phase 4 — First End-to-End Slice

Goal:
Transfer a production quantity into an Area queue.

Workflow:
1. Resolve the scan station context.
2. Scan a part or production quantity.
3. Validate the scan.
4. Record an immutable movement.
5. Derive the current state.
6. Refresh the Scan Station UI.

Completion criteria:
- Valid scans complete successfully.
- Unknown and ambiguous scans are rejected.
- Duplicate requests are handled safely.
- Writes are transactional.
- Relevant tests pass.

## Later Slices

1. Machine assignment
2. Machine sessions
3. Completion allocation
4. Undo/correction workflow
5. Area boards
6. Production tracking
7. Purchase Order intake
8. Stockroom
9. Priority management
10. Administration
11. Authentication and authorization
12. Deployment

## Deferred

- ERP synchronization
- Offline synchronization
- Advanced analytics
- Broad reporting
- Speculative automation
