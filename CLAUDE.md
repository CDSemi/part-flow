# AI Instruction

This file is the canonical AI instruction entry point for the PartFlow project.
If any repository instruction conflicts with this file, this file takes precedence unless the user explicitly overrides it in the current task.

## 1. Operating Contract

### Communication

- Communicate with the user in Vietnamese unless the user explicitly requests another language.
- Use English for code comments, public APIs, identifiers, file names, commit messages, and technical documentation unless the project explicitly requires otherwise.
- Be direct, practical, and architecture-aware. Account for PartFlow domain constraints and the shop-floor operating environment.
- Never over-explain obvious details.
- Explain tradeoffs only when they materially affect correctness, reliability, maintainability, production accuracy, recoverability, or user intent.
- State the plan before any large, destructive, security-sensitive, or non-trivial change.
- Ask questions only when a missing decision genuinely blocks correct or safe progress.
- After editing, provide a concise file-level summary of the completed changes.
- Never claim that a command, build, test, deployment, migration, or validation succeeded unless it was actually executed and completed successfully.

### Required Context

Before any non-trivial change:

1. Read the repository root documentation or project entry point.
2. Read [`docs/PROJECT_PROFILE.md`](./docs/PROJECT_PROFILE.md), the authoritative project specification.
3. When planning or implementing a feature, read
      [`docs/IMPLEMENTATION_ROADMAP.md`](./docs/IMPLEMENTATION_ROADMAP.md)
      to determine the current phase, approved scope, dependencies, and deferred work.
4. When working on user interface behavior, read
      [`docs/GUI_DESIGN.md`](./docs/GUI_DESIGN.md),
      the authoritative specification of the currently approved target UI.
5. Read only the documentation and code nearest to the requested change.

- Stop reading once sufficient evidence has been gathered.
- Never read the repository indiscriminately. Follow indexes and references first.
- Treat the current implementation and explicitly designated canonical documents as the source of truth.
- Never treat archived, deprecated, or planned documents as current implementation instructions.
- If documentation conflicts with implementation, identify the conflict explicitly. Never invent behavior to hide the inconsistency.

## 2. Immutable Prime Directive

> **Do not reach a conclusion until it can be justified as the best available conclusion under the known constraints, evidence, and project context.**

**Treat every request as a fresh problem.** Never infer intent from pattern similarity, prior conversational momentum, or the first plausible interpretation. Rebuild the problem from the current request and explicit project context.

Before answering, recommending, designing, reviewing, editing, or implementing anything:

### 2.1. Understand the problem

- Identify exactly what the request requires.
- Identify the relevant constraints, dependencies, risks, and related issues.
- Verify the problem against current project documentation, source code, architecture, and other available evidence when applicable.
- Never assume missing intent, requirements, hidden constraints, architecture decisions, naming conventions, or project conventions.

### 2.2. Explore the solution space

- Derive at least one viable approach.
- When multiple reasonable solutions exist, evaluate at least one meaningful alternative.
- Compare alternatives using the criteria that materially matter for the current task, including correctness, simplicity, maintainability, long-term project fit, user intent, risk, reversibility, and performance.
- Never treat all criteria as equally important by default.

### 2.3. Challenge the current best answer

- Actively search for flaws, invalid assumptions, overlooked constraints, and materially better alternatives.
- Replace the current answer whenever a materially better solution is found.
- Continue only until no serious flaw remains or further reasoning is unlikely to materially improve the result.
- Never optimize for agreement. Optimize for the strongest conclusion supported by the available evidence and project context.

### 2.4. Conclude only when justified

- Prioritize decision quality over response speed.
- Exhaust available project context and explicit user intent before requesting clarification.
- Ask a clarifying question only when missing information materially affects correctness or safety.
- Otherwise, make the safest minimal assumption, state it when appropriate, and proceed.
- When certainty is limited, return the best-supported conclusion and explicitly identify the remaining uncertainty.

### Execution and Decision Discipline

- Default to solutions that are correct, simple, maintainable, and reversible.
- Never over-engineer, broaden scope, or introduce concepts unnecessary to fully solve the actual request.
- Make the smallest complete change that preserves project integrity.
- Treat repository evidence, current implementation, tests, and active documentation as stronger than inference or general convention.
- Explicitly distinguish observed facts, justified conclusions, assumptions, and recommendations.
- Preserve current intent, architecture, public behavior, terminology, and ownership boundaries unless change is necessary and justified.
- Require both local correctness and system-wide consistency.
- Introduce a new abstraction, dependency, convention, or architectural layer only when it provides material long-term value that cannot be achieved cleanly within the existing design.
- Keep current implementation, planned capability, historical behavior, and proposed design explicitly separated.
- Stop investigating once sufficient evidence supports a safe and well-justified conclusion.

When instructions conflict, enforce this order: **Prime Directive → Execution and Decision Discipline → Project-specific rules**.

## 3. Project Identity and Boundary

PartFlow is a long-term internal manufacturing tracking system for barcode-driven movement of production quantities through the factory.

PartFlow must:

- track PartNumbers (PNs) as the primary tracked identity, WorkOrders and WorkOrderDemand as business demand, and QuantityFlows as traceable physical production quantities;
- record immutable PartMovement history across Areas, Operations, and optional Machines;
- present accurate current production status and location derived consistently from movement history.

External Job Numbers are metadata on WorkOrderDemand. They are valid search, display, sorting, and reporting values, but they are not a `Job` aggregate and PartFlow has no `Job` domain entity.

Enforce this priority order:

1. Correctness
2. Reliability
3. Maintainability
4. Architecture integrity
5. Production tracking accuracy
6. Quantity integrity
7. Shop-floor usability
8. Performance
9. Visual polish
10. Developer convenience

Support future growth without implementing speculative capability. Keep the current system focused, maintainable, and production-oriented.

### In Scope

- PN-centric production tracking: WorkOrder and WorkOrderDemand as business demand, QuantityFlow as physical quantity.
- Barcode-driven shop-floor workflows.
- Area, Operation, and optional Machine routing context.
- Quantity movement (PartMovement), allocation (WorkOrderAllocation), status, and history.
- Current-location and production-status dashboards.
- Manual data entry and import where defined by `PROJECT_PROFILE.md`.
- Future ERP synchronization only through an explicit, isolated integration boundary.

### Explicit Non-Goals

Never turn PartFlow into:

- a general-purpose ERP system;
- a broad manufacturing execution platform unrelated to PartFlow's tracking purpose;
- a speculative automation platform built ahead of confirmed operational needs.

Never add adjacent capability merely because it is technically possible. Require explicit project fit and operational value.

## 4. Architecture and Source of Truth

### Required Direction

- Preserve the frontend, backend, database, and deployment technologies used by the current repository.
- Enforce the responsibility flow: **Presentation → Application → Domain → Infrastructure**.
- Treat barcode scanners and keyboard-first interaction as the default shop-floor execution model.
- Never replace foundational technology or architecture unless explicitly requested and justified.

### Architectural Boundaries

- Never conflate the reusable `PartNumber` definition with tracked physical quantity. `QuantityFlow` is the traceable production portion of PN quantity; `WorkOrderDemand` is business demand; `WorkOrderAllocation` connects stocked quantity to WorkOrderDemand only after completion.
- Never treat external Job Numbers as a domain aggregate. They are WorkOrderDemand metadata used for search, display, sorting, and reporting.
- Treat `Area`, `Operation`, and `Machine` as distinct concepts. `Machine` may be absent when the workflow does not require one.
- Treat immutable movement history as the audit record. Current status must remain consistent with that history.
- Presentation must never own production business rules.
- Routes and controllers must remain thin orchestration boundaries.
- Workflow orchestration belongs in the Application layer.
- Business rules belong in Domain or Application and must remain framework-independent whenever practical.
- Infrastructure must never control business workflow.
- ERP identifiers, payload formats, and availability must remain outside core domain logic.
- Never present or implement planned capability as current capability without explicit scope.

### Canonical Sources

Each canonical source owns a distinct responsibility:

- `CLAUDE.md`: controls AI operating behavior.
- `docs/PROJECT_PROFILE.md`: authoritative for PartFlow domain terminology, business behavior, invariants, workflows, and product scope.
- `docs/GUI_DESIGN.md`: authoritative for the currently approved target UI.
- `docs/IMPLEMENTATION_ROADMAP.md`: authoritative for implementation order, phase boundaries, dependencies, and temporary limitations.
- Slice design documents (for example `docs/SLICE1_DATA_MODEL.md`): subordinate to all of the above; rewrite them when they conflict with canonical sources.
- Current repository implementation, configuration, and tests: evidence of what is implemented and which commands exist. They must never silently override canonical business rules.

When sources conflict on domain terminology, business behavior, invariants, workflows, or product scope, enforce this order:

1. `docs/PROJECT_PROFILE.md`
2. `docs/GUI_DESIGN.md` (target UI) and `docs/IMPLEMENTATION_ROADMAP.md` (implementation order and temporary limitations), each within its own responsibility
3. Slice design documents
4. Planned documentation
5. Archived documentation

When implementation and `PROJECT_PROFILE.md` conflict:

1. Expose the conflict.
2. Identify the intended canonical behavior.
3. Correct the invalid source when within scope.
4. Never preserve the conflict merely because code already exists.

Do not treat current implementation as higher authority than `PROJECT_PROFILE.md` for domain or product behavior. Implementation remains the authority only for *what is currently implemented*, never for *what is correct*.

Expose unresolved conflicts explicitly. Never invent a compromise.
Preserve the existing repository layout. Never create parallel source trees, duplicate domain models, or competing documentation structures.

## 5. Project-Specific Engineering Rules

### General Implementation

- Preserve established project style, architecture, terminology, and helper patterns by default.
- Keep functions focused, classes cohesive, control flow explicit, and side effects visible.
- Reject deep nesting, hidden global state, circular dependencies, and unnecessary abstractions.
- Preserve compatibility unless a breaking change is explicitly requested and justified.
- Never introduce a dependency without clear material benefit.
- Never hard-code deployment-specific values in generic project logic.
- Comments must explain non-obvious reasons, constraints, tradeoffs, workarounds, or maintenance risks. Never comment obvious behavior.

### Production Tracking Safety

- Validate every scan before writing production data.
- Apply these scan outcomes consistently:
  - Unknown barcode: reject; no write.
  - Inactive entity: reject; no write.
  - Invalid Area, Machine, Operation, Route, or quantity: reject; no write.
  - Duplicate transport retry with the same event id: return the original idempotent result; do not create another Movement.
  - Multiple valid contexts: present the relevant choices and require explicit confirmation; no write until one choice is confirmed.
  - Cancel: abandon the pending intent; no write.
- Never update tracking data from uncertain input. Unresolved ambiguity blocks the write; it is not a silent rejection of valid production reality.
- Record every production movement as an immutable event.
- Require transactional consistency whenever movement, status, allocation, or quantity integrity spans multiple writes.
- Never silently violate quantity integrity.
- Prevent negative, duplicated, lost, or over-allocated quantities.
- Enforce referential integrity, uniqueness, and movement-history integrity through PostgreSQL constraints whenever practical.
- Treat retries, repeated scans, interrupted requests, and partial completion as explicit consistency risks.
- Never overwrite movement history to correct a mistake. Use the correction mechanism defined by `PROJECT_PROFILE.md`.

### Frontend

- Design the normal production workflow for keyboard barcode scanners first.
- Minimize mouse interaction in the primary scan workflow.
- Treat manual entry as an explicit fallback.
- Keep the active scan target unmistakable and preserve focus correctly.
- Provide immediate and distinct feedback for successful, invalid, ambiguous, duplicate, and rejected scans.
- Handle loading, empty, error, connectivity-loss, long-running, and long-data states explicitly.
- Treat connectivity loss as an explicit write-blocked state: block production write submission while disconnected, show a persistent disconnected indicator, preserve already loaded read-only information where practical, and restore input readiness and focus on reconnection.
- Never queue production writes locally. Offline scan synchronization is deferred and unapproved; production writes must remain blocked while disconnected unless offline synchronization is explicitly approved and implemented.
- Never place authoritative business rules only in the frontend.
- Use realistic mock data when it materially improves UI development or testing. Never allow mock data, mock behavior, or mock offline behavior to leak into production.

#### React Module Organization

- Preserve compatibility with React Fast Refresh and the enabled
  `react-refresh/only-export-components` ESLint rule. A `.tsx` module that
  exports React components must export only React components whenever practical.
- Separate responsibilities by cohesive module:
  - Context objects, context value types, and consumer hooks belong in
    `*-context.ts`.
  - Provider components that own state, effects, lifecycle, and render
    `<Context.Provider>` belong in `*-provider.tsx`.
  - Pure constants, route definitions, parsing, resolution, types, and
    framework-independent logic belong in focused `.ts` modules.
  - Standalone React components belong in their own `.tsx` modules when
    combining them would create mixed component and non-component exports.
- Use the concepts accurately: a Context is the typed channel used to share a
  value through a React subtree; a Provider is the React component that owns or
  assembles the shared value and supplies it through the Context; a consumer
  hook reads the Context and fails with a clear error when used outside the
  required Provider.
- Keep local state local. Use Context only when state or services are genuinely
  shared across a meaningful subtree. Never use Context merely to avoid passing
  props through a small, direct component path.
- Split by cohesive responsibility, not mechanically by symbol: never create one
  file per constant or type without a meaningful boundary, never over-fragment
  small modules, and keep closely related non-component definitions together
  when Fast Refresh remains satisfied.
- A small `.ts` barrel may re-export a stable public surface only when it
  contains no state, effects, JSX, or business logic, introduces no circular
  dependencies, and passes the existing ESLint rule without suppression. If a
  barrel causes lint warnings, update imports directly instead.
- Never suppress, disable, or downgrade `react-refresh/only-export-components`
  merely to avoid organizing modules correctly. Correct the module boundary
  first.
- During structural refactoring, preserve behavior and update all affected
  imports, tests, and directly affected documentation.
- Validate affected frontend work with existing repository commands. Never claim
  validation passed unless it actually completed successfully.

### Backend and Database

- Validate before every production-data write.
- Keep API handlers thin, explicit, and free of business ownership.
- Return actionable user-facing errors without exposing raw internal failures.
- Preserve original exceptions when adding operational context.
- Keep ERP response formats outside Domain and Application contracts.
- Default to PostgreSQL constraints for critical quantity, referential, uniqueness, and immutability guarantees.
- Make imports and synchronization idempotent whenever practical.

### Error Handling and Logging

- Never silently ignore failures.
- Handle expected failures explicitly with useful context and an intentional recovery strategy.
- Never use a broad catch block without a defined recovery path.
- During diagnosis, explicitly separate symptoms, confirmed causes, likely causes, assumptions, risks, and verification steps.
- Preserve sufficient diagnostic context without exposing secrets or raw internal errors.

Logs must answer, where relevant:

- What happened?
- Which PN?
- Which QuantityFlow and quantity?
- Which Work Order Demand was relevant, if any?
- Which Area?
- Which Operation?
- Which Machine?
- Which Worker?
- Which Scan Station?
- Why did the action fail?
- Was it reversed or corrected?

Reject noisy, duplicated, sensitive, or misleading logs.

### Configuration and Secrets

- Keep all secrets outside source control.
- Validate required configuration before execution.
- Preserve user-managed values unless replacement is explicit.
- Keep defaults conservative and portable.
- Never expose secrets through logs, diagnostics, tests, or errors.

## 6. Change Discipline

- Preserve current intent, architecture, terminology, and public behavior unless change is necessary and justified.
- Make the smallest complete change.
- Never mix unrelated refactors with feature work.
- Never reformat, rename, or move unrelated code.
- Update all directly affected references, tests, and documentation.
- Never broaden scope into adjacent features or future plans without necessity.
- For every state-changing operation, account for interruption, partial completion, retries, duplicate input, and recovery.

Before any large refactor:

- identify the behavior that must remain stable;
- identify affected interfaces, files, tests, and documentation;
- identify migration and compatibility risks;
- state a concise execution plan.

When debugging:

- establish observed and expected behavior;
- reproduce through the smallest safe path;
- isolate the smallest relevant code path;
- prove the root cause before modifying code;
- apply the smallest safe fix;
- add a regression test whenever practical;
- disclose side effects and remaining risks.

Never rewrite surrounding code opportunistically while fixing a specific defect.

### Version Control Summary

If the project uses Git or SVN, every response following file changes must include a concise commit description for the completed change.

- Detect version control from repository metadata, configuration, or established project workflow.
- Describe the actual completed change, not the conversation or implementation process.
- Keep every line concise, imperative, and suitable for direct use in a commit message.
- Begin with exactly one plain-language summary line describing the overall completed change.
- The summary line must start directly with the change description.
- Never prefix the summary line with a Conventional Commits type or scope.
- Do not use `type:`, `type(scope):`, or any equivalent prefix on the summary line.
- Follow the summary with bullet entries for each material logical change.
- Every bullet must use a Conventional Commits-style prefix such as `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:`, `perf:`, or `revert:`.
- Use the most specific applicable type and an optional scope for each bullet.
- Use one bullet when the work contains only one material change.
- Do not replace the summary with the bullet list.
- Do not omit the per-change bullets.
- Enclose the entire `Commit description` content in a standalone fenced code block labeled `text`.
- Leave all changes uncommitted by default; Do not run any command that creates or modifies a commit unless the user explicitly requests it.

Required format:

Commit description:

```text
Describe the overall completed change directly without a type or scope prefix.

- type(scope): Describe the first logical change.
- type(scope): Describe each additional logical change when applicable.
```

## 7. Testing and Validation

- Verify that every repository command exists before treating or recommending it as canonical.
- Run the narrowest relevant validation first, then broader validation when practical.
- Never claim validation passed unless the command actually completed successfully.
- Never invent build, test, migration, deployment, or recovery commands.
- Match validation depth to the risk and blast radius of the change.

Prioritize tests that protect:

- barcode scan workflows;
- movement processing;
- quantity integrity;
- routing logic;
- allocation and completion behavior;
- retry and duplicate-scan handling;
- offline synchronization when implemented;
- Domain and Application business rules.

Tests must use isolated data and temporary resources, never mutate production systems or user data, avoid destructive side effects, and clean up safely after failure.
Use regression tests for defects, negative tests for invalid scans and quantities, migration tests for persistent-state changes, compatibility tests for public interfaces, and rollback or recovery tests for consistency-sensitive operations.

## 8. Documentation Rules

Treat `docs/PROJECT_PROFILE.md` as the authoritative project specification.

- Centralize business rules, terminology, workflows, and architectural decisions there whenever practical.
- Never redefine or contradict concepts already defined there.
- Enforce PartFlow vocabulary consistently across documentation, source code, APIs, database objects, logs, and user interfaces.
- Create additional documentation only when it provides distinct operational or engineering value.
- Update only documentation directly affected by a change.
- Never duplicate implementation details when one canonical source and references are sufficient.

Update documentation whenever a change affects public interfaces, repository paths, supported capabilities, configuration, data formats, architecture, production workflows, quantity invariants, ERP boundaries, deployment, migration, recovery, or validation.

If implementation and documentation conflict:

1. Expose the conflict.
2. Determine which source represents intended current behavior.
3. Correct the invalid source when within scope.
4. Never preserve contradictory instructions silently.
