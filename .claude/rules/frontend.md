---
paths:
  - "frontend/**/*"
---

# Frontend Rules

Apply these rules when working in `frontend/`.

## UI and Production Interaction

- Follow the relevant sections of `docs/GUI_DESIGN.md`; business behavior remains owned by `docs/PROJECT_PROFILE.md`.
- Design Scan Station workflows for keyboard-wedge barcode scanners first. Minimize mouse interaction; manual entry is an explicit fallback.
- Preserve focus discipline: the active scan target must be unmistakable and focus must recover correctly after completed actions, dialog closure, session changes, and reconnection.
- Show distinct, actionable feedback for success, invalid, ambiguous, duplicate, and rejected scans.
- Handle loading, empty, error, disconnected, long-running, narrow-viewport, and long-data states where relevant.
- While disconnected, block production writes, keep the persistent disconnected indication, preserve already loaded read-only data where practical, and restore readiness on reconnection. NEVER queue local production writes.
- NEVER put authoritative production business rules only in the frontend.
- Mock data/behavior is development-only. NEVER allow `src/mocks/` or mock behavior to leak into the production bundle or production API path.
- Preserve semantic design tokens and existing shared presentation primitives instead of introducing parallel styling systems.

## React Module Organization

Preserve compatibility with React Fast Refresh and the enabled `react-refresh/only-export-components` rule.

- A `.tsx` module exporting React components should export only React components whenever practical.
- Context objects, context value types, and consumer hooks belong in focused `*-context.ts` modules.
- Provider components owning state/effects/lifecycle belong in `*-provider.tsx` modules.
- Pure constants, route definitions, parsing, resolution, types, and framework-independent logic belong in focused `.ts` modules.
- Keep local state local. Use Context only when state/services are genuinely shared across a meaningful subtree.
- Split by cohesive responsibility, NOT mechanically one symbol per file. Never over-fragment small modules.
- A small `.ts` barrel is acceptable only when it contains no state, effects, JSX, or business logic and introduces no circular dependency or lint suppression.
- NEVER suppress or downgrade `react-refresh/only-export-components` to avoid fixing module boundaries.
- Preserve the existing lightweight router and production/mock boundary unless the requested change genuinely requires otherwise.

## Validation

The canonical environment is Docker Compose. Run only what is relevant to the change, escalating as needed:

```bash
docker compose exec frontend npm run format:check
docker compose exec frontend npm run lint
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run test
docker compose exec frontend npm run build
```

`npm run build` includes TypeScript checking, the production build, and the mock-boundary check. `npm run check` is the frontend aggregate gate when a broad validation pass is warranted.

NEVER claim any gate passed unless it actually completed successfully.
