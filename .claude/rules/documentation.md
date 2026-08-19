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
- If implementation and documentation conflict, expose the conflict, determine the intended canonical behavior from the owning source, and correct the invalid source when within scope. NEVER preserve a contradiction silently.
- Use PartFlow vocabulary consistently across documentation, code, APIs, database objects, logs, and user-facing copy.
- Technical documentation is written in English unless the project explicitly requires otherwise.
