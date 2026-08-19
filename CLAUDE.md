# PartFlow AI Instruction

This file is the canonical AI instruction entry point for PartFlow.
If any repository instruction conflicts with this file, this file takes precedence unless the user explicitly overrides it in the current task.

## 1. Operating Contract

- Communicate with the user in Vietnamese (xưng "mình" với "bạn") unless explicitly requested otherwise.
- Use English for code comments, public APIs, identifiers, file names, commit messages, and technical documentation unless the project explicitly requires otherwise.
- Be direct, practical, architecture-aware, and shop-floor aware. Never over-explain obvious details.
- Explain tradeoffs only when they materially affect correctness, reliability, maintainability, production accuracy, recoverability, or user intent.
- State a concise plan before large, destructive, security-sensitive, or non-trivial changes.
- Ask questions only when a missing decision genuinely blocks correct or safe progress. Otherwise make the safest minimal assumption and proceed.
- After editing, summarize changed files concisely.
- NEVER claim a command, build, test, deployment, migration, or validation succeeded unless it was actually executed and completed successfully.

## 2. Immutable Prime Directive

> **Do not reach a conclusion until it can be justified as the best available conclusion under the known constraints, evidence, and project context.**

Treat every request as a fresh problem. Never accept the first plausible interpretation merely because it resembles prior work.

Before answering, recommending, designing, reviewing, or implementing:

1. Understand exactly what the request requires and which constraints matter.
2. Verify material assumptions against relevant current documentation, implementation, configuration, and tests.
3. Consider a meaningful alternative when more than one reasonable solution exists.
4. Challenge the current best answer for invalid assumptions, overlooked constraints, unnecessary complexity, and materially better options.
5. Conclude only when the result is sufficiently supported by evidence and project context.

Execution discipline:

- Optimize for correctness, simplicity, maintainability, reversibility, and project fit — not agreement.
- NEVER over-engineer, broaden scope, or introduce concepts unnecessary to solve the actual request.
- Make the smallest complete change that preserves project integrity.
- Preserve current intent, architecture, terminology, public behavior, and ownership boundaries unless change is necessary and justified.
- Introduce a new abstraction, dependency, convention, or architectural layer only when it provides material value that cannot be achieved cleanly within the existing design.
- Keep implemented behavior, planned capability, historical behavior, and proposed design explicitly separate.
- Stop investigating when sufficient evidence supports a safe conclusion.

## 3. Read Only the Context the Task Needs

Before non-trivial work, inspect the nearest relevant code, tests, and configuration. Then load only the canonical documentation sections needed for the decision:

- `docs/PROJECT_PROFILE.md` — domain terminology, business behavior, invariants, workflows, and product scope.
- `docs/GUI_DESIGN.md` — approved target UI only; read the relevant view/global-rule sections for UI work.
- `docs/IMPLEMENTATION_ROADMAP.md` — current implementation state, phase scope, dependencies, and temporary limitations; read the current phase and directly relevant phase(s), not the entire historical narrative unless required.
- Slice design documents such as `docs/SLICE1_DATA_MODEL.md` — implementation detail subordinate to the canonical sources above.
- `README.md` — current repository structure, environment, and verified commands when operational context is needed.

NEVER read large canonical documents indiscriminately. Search headings/terms first, follow direct cross-references, and stop once enough evidence is gathered.
NEVER treat archived, deprecated, superseded, or planned documentation as current implementation instructions.
If documentation and implementation conflict, expose the conflict; never invent behavior to hide it.

## 4. Project Identity and Non-Negotiable Invariants

PartFlow is an internal manufacturing tracking system for barcode-driven movement of production quantities through the factory. It is NOT an ERP, general MES, inventory platform, scheduler, accounting system, or machine-control platform.

Priority order:

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

Core domain rules:

- The canonical PN string is the stable production identity: case-insensitive, normalized to UPPERCASE, with surrounding whitespace trimmed and internal whitespace rejected.
- `PartNumber` is optional current metadata. Production records keep the canonical PN they need and NEVER depend on a surrogate `part_number_id`.
- `WorkOrderDemand` is business demand; `QuantityFlow` is traceable physical production quantity; `WorkOrderAllocation` connects stocked quantity to demand after completion. Never conflate them.
- External Job Numbers are metadata for display/search/sort/reporting, NEVER a `Job` aggregate or workflow identity.
- `Area`, `Operation`, and optional `Machine` are distinct concepts.
- `Worker` is Scan-Station production audit identity; `User` is an application account. NEVER merge them.
- Normal production `PartMovement` history is immutable. Corrections use the canonical compensating/reversal/adjustment mechanism; only the explicit privileged retention workflow defined by `PROJECT_PROFILE.md` may purge verified archived history.
- Current production state must remain consistent with Movement history.
- Quantity must never be accidentally created, destroyed, duplicated, lost, negative, or over-allocated.
- Unknown/invalid input performs no write. Ambiguous input requires explicit confirmation before any write.
- Production writes that span movement, status, allocation, lifecycle, or quantity integrity must be transactional and idempotent where retries are possible.
- Production writes are blocked while disconnected. NEVER invent or queue offline production synchronization unless explicitly approved.
- ERP integration must remain isolated; core behavior must work without ERP and must not depend on ERP payload formats or availability.

## 5. Architecture

Preserve the repository's React + TypeScript frontend, FastAPI backend, PostgreSQL database, Alembic migrations, and Docker Compose workflow unless the user explicitly requests and justifies a foundational change.

Enforce responsibility flow:

**Presentation → Application → Domain → Infrastructure**

- Presentation owns interaction and rendering, NEVER authoritative production business rules.
- Routes/controllers stay thin.
- Application owns workflow orchestration, transactions, and use-case coordination.
- Domain/Application own business rules and should remain framework-independent whenever practical.
- Infrastructure persists and integrates; it NEVER controls business flow.
- ERP identifiers and payload formats stay outside core Domain/Application logic.
- Preserve repository layout; NEVER create parallel source trees, duplicate domain models, or competing documentation structures.

Source authority for intended behavior:

1. `docs/PROJECT_PROFILE.md`
2. `docs/GUI_DESIGN.md` for target UI and `docs/IMPLEMENTATION_ROADMAP.md` for phase/order limitations, each within its own responsibility
3. Slice design documents
4. Planned documentation
5. Archived documentation

Current implementation/configuration/tests are authoritative evidence of what exists now, but they NEVER silently override canonical business rules.

## 6. Change, Error, and Security Discipline

- Preserve established style and helper patterns. Keep functions focused, classes cohesive, control flow explicit, and side effects visible.
- NEVER mix unrelated refactors with requested work. Never reformat, rename, or move unrelated code.
- Update directly affected references, tests, and documentation only.
- NEVER add a dependency without clear material benefit.
- NEVER silently ignore failures or use a broad catch without a defined recovery path.
- Preserve original exceptions when adding operational context; expose actionable user-facing errors without leaking raw internals.
- Keep secrets outside source control, logs, diagnostics, tests, and error responses.
- For state-changing work, account for interruption, partial completion, retries, duplicate input, and recovery.

When debugging: establish observed vs expected behavior, reproduce through the smallest safe path, isolate and prove the root cause, apply the smallest safe fix, and add a regression test when practical.

## 7. Testing and Validation

- Verify repository commands before treating them as canonical; prefer commands documented in `README.md` and current package/tool configuration.
- Use the Linux Docker Compose environment as the canonical quality-gate environment when practical.
- Run the narrowest relevant validation first, then broader validation proportional to risk.
- NEVER invent commands or claim validation passed unless it actually completed successfully.
- Tests must use isolated data/resources and must never mutate production or user data.
- Prefer regression tests for defects, negative tests for invalid scans/quantities, migration tests for persistent-state changes, compatibility tests for public interfaces, and recovery tests for consistency-sensitive operations.

Path-specific frontend and backend validation rules are in `.claude/rules/` and load only when relevant files are read.

## 8. Version Control Summary

After file changes, every response must include a concise `Commit description` for the completed work. Leave changes uncommitted by default; NEVER create or modify a commit unless the user explicitly requests it.

Required format:

Commit description:

```text
Describe the overall completed change directly without a type or scope prefix.

- type(scope): Describe the first logical change.
- type(scope): Describe each additional logical change when applicable.
```

Rules:

- The first line is plain-language imperative text with NO Conventional Commits prefix.
- Follow it with one bullet per material logical change.
- Every bullet uses the most specific Conventional Commits-style prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:`, `perf:`, or `revert:`), with optional scope.
- Enclose the entire commit description in one standalone fenced `text` block.
