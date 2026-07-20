# AI Instruction

This is the canonical AI instruction entry point for the PartFlow project.
If another repository instruction file conflicts with this file, follow this file unless the user explicitly overrides it in the current task.

## 1. Operating Contract

### Communication

- Chat with the user in Vietnamese unless the user explicitly requests another language.
- Code comments, public APIs, identifiers, file names, commit messages, and technical documentation must be in English unless the project explicitly requires otherwise.
- Be direct, practical, and aware of the project's architecture, domain constraints, and shop-floor operating environment.
- Do not over-explain obvious things.
- Explain tradeoffs only when they materially affect correctness, reliability, maintainability, production accuracy, recoverability, or user intent.
- Explain the plan before large, destructive, security-sensitive, or non-trivial changes.
- Ask questions only when a missing decision genuinely blocks correct or safe progress.
- After editing, provide a concise file-level summary of what changed.
- Never claim a command, build, test, deployment, migration, or validation succeeded unless it was actually run.

### Read First

Before non-trivial changes:

1. Read the repository root documentation or project entry point.
2. Read [`docs/PROJECT_PROFILE.md`](./docs/PROJECT_PROFILE.md), the authoritative project specification.
3. Read only the documentation and code nearest to the area being changed.

- Stop reading once sufficient evidence has been gathered.
- Do not read every document blindly. Follow indexes and references first.
- Treat current implementation and explicitly designated canonical documents as the source of truth.
- Do not use archived, deprecated, or planned documents as current implementation instructions.
- If documentation conflicts with implementation, identify the conflict instead of silently inventing behavior.

## 2. Immutable Prime Directive

> **Do not reach a conclusion until it can be justified as the best available conclusion under the known constraints, evidence, and project context.**

**Treat every request as a fresh problem**. Do not infer intent from pattern similarity, prior conversational momentum, or the first plausible interpretation. Rebuild your understanding from the current request together with explicit project context.

Before answering, recommending, designing, reviewing, editing, or implementing anything:

### 2.1. Understand the problem

- Identify what the request is actually asking.
- Identify relevant constraints, dependencies, risks, and related issues.
- Verify against existing project documentation, source code, architecture, and other available context when applicable.
- Do not assume missing intent, requirements, hidden constraints, architecture decisions, naming conventions, or project conventions.

### 2.2. Explore the solution space

- Reason through a viable approach.
- When multiple reasonable solutions exist, consider at least one meaningful alternative.
- Compare approaches using the criteria that matter for the current task, such as correctness, simplicity, maintainability, long-term project fit, user intent, risk, reversibility, and performance.
- Do not assume all criteria are equally important.

### 2.3. Challenge the current best answer

- Actively look for flaws, invalid assumptions, overlooked constraints, or superior alternatives.
- Replace the current answer if a materially better solution is found.
- Continue only until no serious flaw remains or further reasoning is unlikely to materially improve the result.
- Do not optimize for agreement. Optimize for the best conclusion supported by the available evidence and project context.

### 2.4. Conclude only when justified

- Prioritize decision quality over response speed.
- Use available project context and the user's explicit intent before asking for clarification.
- Ask a clarifying question only when missing information materially affects correctness or safety.
- Otherwise, make the safest minimal assumption, state it when appropriate, and proceed.
- If certainty is limited, return the best supported answer and identify the remaining uncertainty.

### Execution and Decision Discipline

- Prefer solutions that are correct, simple, maintainable, and reversible.
- Do not over-engineer, broaden scope, or introduce concepts unnecessary to solve the actual request.
- Make the smallest complete change that preserves project integrity.
- Prefer repository evidence, current implementation, tests, and active documentation over inference or general convention.
- Distinguish observed facts, justified conclusions, assumptions, and recommendations.
- Preserve current intent, architecture, public behavior, terminology, and ownership boundaries unless change is necessary and justified.
- Evaluate both local correctness and system-wide consistency.
- Introduce a new abstraction, dependency, convention, or architectural layer only when it provides a material long-term benefit that cannot be achieved cleanly within the existing design.
- Separate current implementation, planned capability, historical behavior, and proposed design.
- Stop investigating once sufficient evidence supports a safe and well-justified conclusion.

If instructions conflict, follow this order: **Prime Directive → Execution and Decision Discipline → Project-specific rules**.

## 3. Project Identity and Boundary

PartFlow is a long-term internal manufacturing tracking system for recording production quantities as they move through the factory using barcode-driven workflows.

Its primary responsibilities are:

- tracking Jobs, Parts, and production quantities;
- recording immutable movement history across Areas, Operations, and optional Machines;
- presenting accurate current production status and location derived consistently from movement history.

The project prioritizes:

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

Support future growth without prematurely building it now.
Keep the current implementation focused, maintainable, and production-oriented.

### In Scope

- Job and production-part tracking.
- Barcode-driven shop-floor workflows.
- Area, Operation, and optional Machine routing context.
- Quantity movement, allocation, status, and history.
- Current-location and production-status dashboards.
- Manual data entry and import where defined by `PROJECT_PROFILE.md`.
- Isolated future ERP synchronization through an explicit integration boundary.

### Explicit Non-Goals

Do not turn PartFlow into:

- a general-purpose ERP system;
- a broad manufacturing execution platform unrelated to PartFlow's tracking purpose;
- a speculative automation platform built ahead of confirmed operational needs.

Do not add adjacent capabilities merely because they are technically possible. Require clear project fit and operational value.

## 4. Architecture and Source of Truth

### Required Direction

- Follow the frontend, backend, database, and deployment technologies used by the current repository.
- Preserve the layered responsibility flow: **Presentation → Application → Domain → Infrastructure**.
- Design normal shop-floor execution for barcode scanners and keyboard-first interaction.
- Do not replace foundational technology or architecture unless explicitly requested and justified.

### Architectural Boundaries

- A reusable `Part` definition is not the same as a tracked production instance associated with a `Job`.
- `Area`, `Operation`, and `Machine` are distinct concepts; `Machine` may be absent where the workflow does not require one.
- Immutable movement history is the audit record; current status must remain consistent with that history.
- Presentation must not own production business rules.
- Routes and controllers must remain thin.
- Workflow orchestration belongs in the Application layer.
- Business rules belong in Domain or Application and should remain framework-independent whenever practical.
- Infrastructure must not control business workflow.
- ERP identifiers, payload formats, and availability must remain outside core domain logic.
- Planned capabilities must not be presented or implemented as current capabilities without explicit scope.

### Canonical Sources

- `CLAUDE.md`: canonical AI instruction entry point.
- `docs/PROJECT_PROFILE.md`: authoritative business model, terminology, workflows, architecture, and design specification.
- Current repository implementation, configuration, and tests: authoritative evidence of implemented behavior and available commands.

When sources conflict, use this order:

1. Current implementation and stable user-facing or operator-facing behavior
2. `docs/PROJECT_PROFILE.md` and other current canonical documentation
3. Planned documentation
4. Archived documentation

Identify unresolved conflicts explicitly. Do not invent a compromise.
Use the existing repository layout and do not create parallel source trees, duplicate domain models, or competing documentation structures.

## 5. Project-Specific Engineering Rules

### General Implementation

- Follow existing project style, architecture, terminology, and helper patterns first.
- Keep functions focused, classes cohesive, control flow explicit, and side effects visible.
- Avoid deep nesting, hidden global state, circular dependencies, and unnecessary abstractions.
- Preserve compatibility unless a breaking change is explicitly requested and justified.
- Do not introduce a dependency without clear material benefit.
- Do not hard-code deployment-specific values in generic project logic.
- Comments should explain non-obvious reasons, constraints, tradeoffs, workarounds, or maintenance risks—not obvious code behavior.

### Production Tracking Safety

- Validate every scan before writing production data.
- Reject unknown, ambiguous, duplicate, or context-invalid scans clearly.
- Never update tracking data from uncertain input.
- Record production movement as an immutable event.
- Use transactions when movement, status, allocation, or quantity consistency spans multiple writes.
- Never silently violate quantity integrity.
- Prevent negative, duplicated, lost, or over-allocated quantities.
- Preserve referential integrity, uniqueness, and movement-history integrity through database constraints when practical.
- Treat retries, repeated scans, interrupted requests, and partial completion as explicit consistency risks.
- Do not overwrite movement history to correct a mistake; follow the correction mechanism defined by `PROJECT_PROFILE.md`.

### Frontend

- Design the normal production workflow for keyboard barcode scanners first.
- Avoid mouse interaction in the primary scan workflow.
- Support manual entry as an explicit fallback.
- Keep the active scan target obvious and preserve focus correctly.
- Provide immediate and distinct feedback for successful, invalid, ambiguous, duplicate, and rejected scans.
- Handle loading, empty, error, offline, long-running, and long-data states.
- Do not place authoritative business rules only in the frontend.
- Use realistic mock data when it improves UI development or testing, but never allow mock behavior or data to leak into production.

### Backend and Database

- Validate before writing production data.
- Keep API handlers thin and explicit.
- Return actionable user-facing errors without exposing raw internal failures.
- Preserve original exceptions when adding operational context.
- Keep ERP response formats outside Domain and Application contracts.
- Prefer PostgreSQL constraints for critical quantity, referential, uniqueness, and immutability guarantees.
- Make imports and synchronization idempotent whenever practical.

### Error Handling and Logging

- Never silently ignore failures.
- Handle expected failures explicitly with useful context and an intentional recovery strategy.
- Do not use broad catch blocks without a clear recovery path.
- Distinguish symptoms, confirmed causes, likely causes, assumptions, risks, and verification steps when diagnosing issues.
- Preserve enough diagnostic context without exposing secrets or raw internal errors.

Logs should answer:

- What happened?
- Which Job?
- Which Part or tracked production item?
- Which Area?
- Which Operation?
- Which Machine, when applicable?
- Which quantity was affected?
- Why did it fail?

Avoid noisy, duplicated, sensitive, or misleading logs.

### Configuration and Secrets

- Keep secrets outside source control.
- Validate required configuration before execution.
- Preserve user-managed values unless replacement is explicit.
- Keep defaults conservative and portable.
- Do not expose secrets through logs, diagnostics, tests, or errors.

## 6. Change Discipline

- Preserve current intent, architecture, terminology, and public behavior unless change is justified.
- Make the smallest complete change.
- Do not mix unrelated refactors with feature work.
- Do not reformat, rename, or move unrelated code.
- Update directly affected references, tests, and documentation.
- Do not broaden scope into adjacent features or future plans without need.
- For state-changing operations, account for interruption, partial completion, retries, duplicate input, and recovery.

Before a large refactor:

- identify the behavior that must remain stable;
- identify affected interfaces, files, tests, and documentation;
- describe migration or compatibility concerns;
- provide a short plan.

When debugging:

- identify observed and expected behavior;
- reproduce through the smallest safe path;
- locate the smallest relevant code path;
- identify the root cause;
- apply the smallest safe fix;
- add a regression test when practical;
- disclose side effects and remaining risks.

Do not randomly rewrite surrounding code while fixing one defect.

### Version Control Summary

If the project uses Git or SVN, every response following file changes must include a concise commit description for the completed change.

- Detect version control from repository metadata, configuration, or established project workflow.
- Use a Conventional Commits-style type prefix such as `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:`, `perf:`, or `revert:`.
- Use the most specific applicable type and an optional scope when useful.
- Describe the actual completed change, not the conversation or implementation process.
- Keep each description concise, imperative, and suitable for direct use as a commit message.
- Begin with a single summary line that describes the overall completed change using the same Conventional Commits-style format.
- Follow the summary with bullet entries for each material change. Use one bullet when the work contains only one material change.
- Do not replace the summary with the bullet list or omit the per-change bullets.
- Enclose the entire `Commit description` section in a standalone fenced code block labeled `text` so it can be copied directly and distinguished from the rest of the response.

Required format:

Commit description:

```text
Short summary of the completed change

Changes:
- type(scope): first material change
- type(scope): second material change (if applicable)
...
```

## 7. Testing and Validation

- Verify repository commands exist before treating or recommending them as canonical.
- Run the narrowest relevant tests first, then broader validation when practical.
- Never claim validation passed unless the command actually completed successfully.
- Do not invent build, test, migration, deployment, or recovery commands.
- Match validation depth to the risk of the change.

Prioritize tests for:

- barcode scan workflows;
- movement processing;
- quantity integrity;
- routing logic;
- allocation and completion behavior;
- retry and duplicate-scan handling;
- offline synchronization when implemented;
- Domain and Application business rules.

Tests must use isolated data and temporary resources, avoid production systems and destructive side effects, and clean up safely after failure.
Use regression tests for defects, negative tests for invalid scans and quantities, migration tests for persistent-state changes, compatibility tests for public interfaces, and rollback or recovery tests for consistency-sensitive operations.

## 8. Documentation Rules

`docs/PROJECT_PROFILE.md` is the authoritative project specification.

- Keep business rules, terminology, workflows, and architectural decisions centralized there whenever practical.
- Do not redefine or contradict concepts already defined there.
- Use PartFlow vocabulary consistently across documentation, source code, APIs, database objects, logs, and user interfaces.
- Create additional documentation only when it provides distinct operational or engineering value.
- Update only documents directly affected by a change.
- Do not duplicate implementation details when one canonical source and references are sufficient.

Update documentation when a change affects public interfaces, repository paths, supported capabilities, configuration, data formats, architecture, production workflows, quantity invariants, ERP boundaries, deployment, migration, recovery, or validation.

If implementation and documentation conflict:

1. Mention the conflict.
2. Determine which source represents intended current behavior.
3. Update the incorrect source when within scope.
4. Do not silently preserve contradictory instructions.
