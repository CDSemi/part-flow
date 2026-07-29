// Framework-independent grouping and statistics for the shared Area /
// Machine monitoring surfaces (Area Board per-Area detail and the Scan
// Station "In this Area now" layout). Production-safe: no mock data,
// no business rules — presentation grouping only.

import type { MockArea, MockAreaCard } from './view-models';

export const isQueueContext = (name: string) =>
  name === 'queue' || name === 'vendor';

export interface AreaAssignment {
  card: MockAreaCard;
  context: string;
  qty: number;
}

/**
 * Split each card's quantity into Machine assignments and queue /
 * direct-processing portions. Every piece appears exactly once —
 * quantities are never duplicated or lost by the grouping.
 */
export function splitAssignments(cards: readonly MockAreaCard[]): {
  assigned: AreaAssignment[];
  queued: AreaAssignment[];
} {
  const assigned: AreaAssignment[] = [];
  const queued: AreaAssignment[] = [];
  for (const card of cards) {
    if (card.machines.length === 0) {
      queued.push({ card, context: '—', qty: card.qty });
      continue;
    }
    for (const [context, qty] of card.machines) {
      if (isQueueContext(context)) queued.push({ card, context, qty });
      else assigned.push({ card, context, qty });
    }
  }
  return { assigned, queued };
}

export interface AreaStat {
  value: number | string;
  label: string;
  tone?: 'q' | 'm' | 'h';
}

/**
 * Meaningful Area statistics only (no zero-value queue/Machine noise):
 * Areas with Machines show Total PNs · Total pcs · Queued · On machines
 * · Hot; Areas without Machines show Total PNs · Total pcs · Processing
 * · Hot; the terminal Stockroom shows PNs · Stocked pcs · Hot.
 */
export function areaStats(
  area: MockArea,
  cards: readonly MockAreaCard[],
  hasMachines: boolean,
): AreaStat[] {
  const totalQty = cards.reduce((s, c) => s + c.qty, 0);
  const hotCount = cards.filter((c) => c.hotRank !== undefined).length;
  const { assigned, queued } = splitAssignments([...cards]);
  const queuedQty = queued.reduce((s, e) => s + e.qty, 0);
  const machineQty = assigned.reduce((s, e) => s + e.qty, 0);
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
      { value: totalQty, label: 'Processing', tone: 'm' },
      { value: hotCount || '—', label: 'Hot', tone: 'h' },
    ];
  }
  return [
    { value: cards.length, label: 'Total PNs' },
    { value: totalQty, label: 'Total pcs' },
    { value: queuedQty, label: 'Queued', tone: 'q' },
    { value: machineQty, label: 'On machines', tone: 'm' },
    { value: hotCount || '—', label: 'Hot', tone: 'h' },
  ];
}

/** Group heading for the non-Machine portion of an Area's quantity. */
export function directGroupLabel(area: MockArea, hasMachines: boolean): string {
  if (area.terminal) return 'Stocked';
  return hasMachines ? 'Area queue — awaiting Machine' : 'In processing';
}
