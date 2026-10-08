// One-shot hand-off from Tracking's `Change priority` to Management →
// Priority (GUI_DESIGN §8 arrival; Phase 14 slice 7, OD-P18).
//
// A module-scoped value (the `hot-history.ts` precedent; the router has
// no query parameters): Tracking requests the PN right after it
// navigated to Priority, Priority reads it once when it mounts and
// clears it, so a later visit sees nothing. A hand-off Priority never
// read (its view failed to load, or the user left first) ends when the
// user leaves Priority, signs out or another user signs in — never
// offered to a later visit or another user. Nothing here is a write —
// the arrival only highlights, opens the Add dialog or says why not.

import { useEffect, useRef } from 'react';

import { useRouter } from '../../app/router-context';
import { useSession } from '../../app/session-context';

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

/**
 * End an unread hand-off when the route leaves Priority, the user signs
 * out or another user signs in (an ended sign-in renewed by the same
 * user keeps it). Called once by the always-mounted application shell.
 */
export function usePriorityFocusReset() {
  const { route } = useRouter();
  const { status, user, endedBy } = useSession();
  const onPriority =
    route.view === 'management' && route.subview === 'priority';
  const userId = status === 'signed-in' ? (user?.id ?? null) : null;
  const signedOut = status === 'signed-out' && endedBy === 'sign-out';
  const owner = useRef<number | null>(null);
  useEffect(() => {
    if (!onPriority) clearPriorityFocus();
  }, [onPriority]);
  useEffect(() => {
    if (userId !== null) {
      if (owner.current !== null && owner.current !== userId) {
        clearPriorityFocus();
      }
      owner.current = userId;
    } else if (signedOut) {
      clearPriorityFocus();
    }
  }, [userId, signedOut]);
}
