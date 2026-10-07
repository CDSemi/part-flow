// Session Undo/Redo history of the Hot list (GUI_DESIGN §8;
// PROJECT_PROFILE §21 Priority Management item 9).
//
// The history is a module-scoped store: it survives Management sub-view
// switches (the Priority view unmounts and remounts) and ends with the
// page session. It belongs to the signed-in user (Phase 14 slice 3): it
// also ends when that user signs out or another user signs in, so it
// never offers one user's steps to another; an ended sign-in renewed by
// the same user keeps it. Depth is unlimited — no numeric cap is ever
// applied.
//
// A step is an INTENT about one Hot entry, never a stored full order:
// pressing Undo/Redo re-bases the step's operation on the list as it is
// rendered NOW, so a change made elsewhere meanwhile is never silently
// reverted. The server stays the authority — every step is applied
// through the same confirmed, audited Hot list command as a new change.
//
// Production-safe: no mock data.

import { useEffect, useSyncExternalStore } from 'react';

import type { HotListEntry } from '../../api/hot-list';
import { useSession } from '../../app/session-context';

/** One single-entry operation on the current order. */
export type HistoryOp =
  | { kind: 'insert'; index: number }
  | { kind: 'remove' }
  | { kind: 'move'; toIndex: number };

export interface HistoryStep {
  demandId: number;
  /** The entry as it was when the change was confirmed — names it in
   * confirmations and notices while it is not on the list. */
  entrySnapshot: HotListEntry;
  undo: HistoryOp;
  redo: HistoryOp;
}

export interface HotHistory {
  /** Oldest first; Undo applies the last step. */
  undo: readonly HistoryStep[];
  /** Oldest first; Redo applies the last step. */
  redo: readonly HistoryStep[];
}

/** The outcome of re-basing one operation on the current order. */
export type RebasedOp =
  | { kind: 'order'; newOrder: number[] }
  /** The entry already sits where the step would put it. */
  | { kind: 'noop' }
  /** The entry is already listed (insert) or no longer listed
   * (remove/move) — the step cannot be applied to this list. */
  | { kind: 'inapplicable' };

/**
 * Apply `op` for `demandId` to `current` (demand ids in rank order). An
 * insert index is clamped to the list length, a move target to the
 * last position.
 */
export function rebaseOp(
  op: HistoryOp,
  demandId: number,
  current: readonly number[],
): RebasedOp {
  const index = current.indexOf(demandId);
  if (op.kind === 'insert') {
    if (index !== -1) return { kind: 'inapplicable' };
    const at = Math.min(Math.max(op.index, 0), current.length);
    return {
      kind: 'order',
      newOrder: [...current.slice(0, at), demandId, ...current.slice(at)],
    };
  }
  if (index === -1) return { kind: 'inapplicable' };
  if (op.kind === 'remove') {
    return { kind: 'order', newOrder: current.filter((id) => id !== demandId) };
  }
  const to = Math.min(Math.max(op.toIndex, 0), current.length - 1);
  if (to === index) return { kind: 'noop' };
  const next = current.filter((id) => id !== demandId);
  next.splice(to, 0, demandId);
  return { kind: 'order', newOrder: next };
}

// ---------------------------------------------------------------------------
// Module-scoped store
// ---------------------------------------------------------------------------

const EMPTY: HotHistory = { undo: [], redo: [] };

let history: HotHistory = EMPTY;
/** The user the history belongs to (the last signed-in user seen). */
let historyOwner: number | null = null;
const listeners = new Set<() => void>();

function update(next: HotHistory) {
  history = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const snapshot = () => history;

/** The current session history; re-renders on every change. */
export function useHotHistory(): HotHistory {
  return useSyncExternalStore(subscribe, snapshot);
}

/** A confirmed NEW change: pushed onto Undo; Redo is cleared. */
export function recordChange(step: HistoryStep) {
  update({ undo: [...history.undo, step], redo: [] });
}

/** A confirmed (or already satisfied) Undo: the step moves to Redo. */
export function completeUndo(step: HistoryStep) {
  update({
    undo: history.undo.filter((s) => s !== step),
    redo: [...history.redo, step],
  });
}

/** A confirmed (or already satisfied) Redo: the step moves to Undo. */
export function completeRedo(step: HistoryStep) {
  update({
    undo: [...history.undo, step],
    redo: history.redo.filter((s) => s !== step),
  });
}

/** A step that can no longer be applied leaves both stacks. */
export function dropStep(step: HistoryStep) {
  update({
    undo: history.undo.filter((s) => s !== step),
    redo: history.redo.filter((s) => s !== step),
  });
}

/** Forget the whole history (a fresh session; isolated tests). */
export function clearHotHistory() {
  historyOwner = null;
  update(EMPTY);
}

/**
 * Keep the history with the user it belongs to: forget it when another
 * user signs in or the user signs out. Called once by the always-mounted
 * application shell.
 */
export function useHotHistoryOwnerReset() {
  const { status, user, endedBy } = useSession();
  const userId = status === 'signed-in' ? (user?.id ?? null) : null;
  const signedOut = status === 'signed-out' && endedBy === 'sign-out';
  useEffect(() => {
    if (userId !== null) {
      if (historyOwner !== null && historyOwner !== userId) clearHotHistory();
      historyOwner = userId;
    } else if (signedOut) {
      clearHotHistory();
    }
  }, [userId, signedOut]);
}
