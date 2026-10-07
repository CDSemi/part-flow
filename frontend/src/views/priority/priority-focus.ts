// One-shot hand-off from Tracking's `Change priority` to Management →
// Priority (GUI_DESIGN §8 arrival; Phase 14 slice 7, OD-P18).
//
// A module-scoped value (the `hot-history.ts` precedent; the router has
// no query parameters): Tracking requests the PN right after it
// navigated to Priority, Priority reads it once when it mounts and
// clears it, so a later visit sees nothing. Nothing here is a write —
// the arrival only highlights, opens the Add dialog or says why not.
//
// Pure: no React, no framework imports.

let pending: string | null = null;

/** Hand one PN to the next Priority mount (replaces a pending one). */
export function requestPriorityFocus(pn: string): void {
  pending = pn;
}

/** The pending PN, unchanged (an idempotent, StrictMode-safe read). */
export function peekPriorityFocus(): string | null {
  return pending;
}

export function clearPriorityFocus(): void {
  pending = null;
}
