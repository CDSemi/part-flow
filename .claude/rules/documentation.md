---
paths:
  - "docs/**/*"
  - "README.md"
---

# Documentation Rules

Apply these rules when reading or editing project documentation.

- `docs/PROJECT_PROFILE.md` owns domain terminology, business behavior, invariants, workflows, and product scope.
- `docs/GUI_DESIGN.md` owns the approved target UI. Do NOT redefine business rules there when a reference to `PROJECT_PROFILE.md` is sufficient.
- `docs/IMPLEMENTATION_ROADMAP.md` owns implementation order, phase boundaries, dependencies, current implementation state, and temporary limitations.
- Slice documents are subordinate implementation detail and must not contradict the canonical sources.
- Archived/superseded documents are historical reference only, NEVER current instructions.
- Centralize each rule in its owning source and reference it elsewhere instead of duplicating it.
- Update only documentation directly affected by the change. NEVER create parallel or competing documentation structures without a concrete need.
- Keep current implementation, target design, future plan, and historical behavior explicitly distinct.
- A document states only its current content. When something changes, rewrite the affected text in place; NEVER append a dated status narrative, slice log, audit log, or "formerly …" note beside the old text. Detailed history lives in git.
- Every document ends with a `## Change history` section (`# Change history` in a document whose top-level headings use a single `#`) holding one table (columns: Date, Commit, Change), newest row first, one short sentence per row. A row for a change that is not committed yet says `uncommitted` in the Commit column; the next edit of that document replaces it with the commit hash. A Vietnamese mirror translates it as `## Lịch sử thay đổi` (Ngày, Commit, Thay đổi). Documents written before this rule are brought into line when they are next rewritten.
- Write for a reader who is new to the project: complete sentences and complete steps, no telegraphic shorthand, every abbreviation spelled out at first use (for example "recovery point objective (RPO)"), and every named item given by its text first with any code only in parentheses as a reference.
- If implementation and documentation conflict, expose the conflict, determine the intended canonical behavior from the owning source, and correct the invalid source when within scope. NEVER preserve a contradiction silently.
- Use PartFlow vocabulary consistently across documentation, code, APIs, database objects, logs, and user-facing copy.
- Technical documentation is written in English unless the project explicitly requires otherwise.
