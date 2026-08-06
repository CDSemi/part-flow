// Framework-independent grouping and statistics for the shared Area /
// Machine monitoring surfaces (Area Board per-Area detail and the Scan
// Station "In this Area now" layout). Production-safe: no mock data,
// no business rules — presentation grouping only.

import type { MockArea, MockAreaCard } from './view-models';

export const isQueueContext = (name: string) =>
  name === 'queue' || name === 'vendor';

/**
 * Explicit presentation state of one PN presence portion:
 * - `machine` — actively assigned to the named Machine;
 * - `queue` — waiting in the Area queue for a Machine;
 * - `vendor` — at an outside-processing vendor (External);
 * - `direct` — direct Area processing (no Machines) or terminal stock;
 * - `finished` — completed Area processing, waiting on the finished
 *   rack for transfer (`READY_TO_TRANSFER`); the context names the
 *   Machine that completed the work (completion context only).
 */
export type AreaAssignmentState =
  'machine' | 'queue' | 'vendor' | 'direct' | 'finished';

export interface AreaAssignment {
  card: MockAreaCard;
  /** Machine name, `queue`/`vendor`, or `—` for direct portions. */
  context: string;
  qty: number;
  state: AreaAssignmentState;
}

/**
 * Split each card's quantity into Machine assignments, queue /
 * direct-processing portions, and finished (`READY_TO_TRANSFER`)
 * portions. Every piece appears exactly once — quantities are never
 * duplicated or lost by the grouping. In a no-Machine Area the direct
 * processing portion is the remainder after the finished portions.
 */
export function splitAssignments(cards: readonly MockAreaCard[]): {
  assigned: AreaAssignment[];
  queued: AreaAssignment[];
  finished: AreaAssignment[];
} {
  const assigned: AreaAssignment[] = [];
  const queued: AreaAssignment[] = [];
  const finished: AreaAssignment[] = [];
  for (const card of cards) {
    const finishedQty = (card.finished ?? []).reduce((s, f) => s + f.qty, 0);
    if (card.machines.length === 0) {
      const directQty = card.qty - finishedQty;
      if (directQty > 0 || finishedQty === 0) {
        queued.push({ card, context: '—', qty: directQty, state: 'direct' });
      }
    } else {
      for (const [context, qty] of card.machines) {
        if (isQueueContext(context)) {
          queued.push({
            card,
            context,
            qty,
            state: context === 'vendor' ? 'vendor' : 'queue',
          });
        } else {
          assigned.push({ card, context, qty, state: 'machine' });
        }
      }
    }
    for (const portion of card.finished ?? []) {
      finished.push({
        card,
        context: portion.completedBy ?? '—',
        qty: portion.qty,
        state: 'finished',
      });
    }
  }
  return { assigned, queued, finished };
}

export interface AreaStat {
  value: number | string;
  label: string;
  tone?: 'q' | 'm' | 'd' | 'h';
}

/**
 * Shared Area statistics with the shared semantic tone mapping
 * (`q` warning · `m` information · `d` success · `h` error). The
 * plain totals (Total PNs, Total pcs) carry no tone: they share ONE
 * muted neutral — the same color as the Machine-card totals line —
 * with no per-meaning color split. The quantity statistics reconcile —
 * Areas with Machines: `Total pcs = Queued + On machines + Done`;
 * Areas without Machines: `Total pcs = Processing + Done`; the
 * terminal Stockroom shows stocked totals instead. Hot renders `—`
 * when zero; the reconciling quantity values always render as numbers.
 */
export function areaStats(
  area: MockArea,
  cards: readonly MockAreaCard[],
  hasMachines: boolean,
): AreaStat[] {
  const totalQty = cards.reduce((s, c) => s + c.qty, 0);
  const hotCount = cards.filter((c) => c.hotRank !== undefined).length;
  const { assigned, queued, finished } = splitAssignments(cards);
  const queuedQty = queued.reduce((s, e) => s + e.qty, 0);
  const machineQty = assigned.reduce((s, e) => s + e.qty, 0);
  const finishedQty = finished.reduce((s, e) => s + e.qty, 0);
  if (area.terminal) {
    return [
      { value: cards.length, label: 'PNs' },
      { value: totalQty, label: 'Stocked pcs' },
      { value: hotCount || '—', label: 'Hot', tone: 'h' },
    ];
  }
  if (!hasMachines) {
    return [
      { value: cards.length, label: 'Total PNs' },
      { value: totalQty, label: 'Total pcs' },
      { value: queuedQty, label: 'Processing', tone: 'm' },
      { value: finishedQty, label: 'Done', tone: 'd' },
      { value: hotCount || '—', label: 'Hot', tone: 'h' },
    ];
  }
  return [
    { value: cards.length, label: 'Total PNs' },
    { value: totalQty, label: 'Total pcs' },
    { value: queuedQty, label: 'Queued', tone: 'q' },
    { value: machineQty, label: 'On machines', tone: 'm' },
    { value: finishedQty, label: 'Done', tone: 'd' },
    { value: hotCount || '—', label: 'Hot', tone: 'h' },
  ];
}

/** Group heading for the non-Machine portion of an Area's quantity. */
export function directGroupLabel(area: MockArea, hasMachines: boolean): string {
  if (area.terminal) return 'Stocked';
  return hasMachines ? 'Area queue — awaiting Machine' : 'In processing';
}

/** Group heading for finished (`READY_TO_TRANSFER`) quantity. */
export const FINISHED_GROUP_LABEL = 'Finished — ready to move';
