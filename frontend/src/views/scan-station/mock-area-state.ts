// Development-only mock state transitions for the Scan Station.
//
// Phase 2 has no backend: the Scan Station demonstrates the approved
// interaction model against a session-local copy of the mock Area
// cards. These pure helpers apply one confirmed application command to
// that copy so the monitoring surfaces reflect the result (quantity
// leaves a Machine card after DONE, appears under `Finished — ready to
// move`, transfers consume the source, …). They implement presentation
// behavior only — every production rule stays server-side in later
// phases — and they are never imported by production-safe modules.

import { isQueueContext } from '../area-monitoring';
import type { AreaKey, MockAreaCard } from '../view-models';
import { pnKey } from './barcode';

/** How one card's quantity is currently held. */
export interface CardBreakdown {
  /** Finished at the Area — `READY_TO_TRANSFER` on the finished rack. */
  finished: number;
  /** Waiting in the Area queue (Areas with Machines). */
  queued: number;
  /** Actively processing: on Machines, at a vendor, or direct. */
  active: number;
}

export function cardBreakdown(card: MockAreaCard): CardBreakdown {
  const finished = (card.finished ?? []).reduce((s, f) => s + f.qty, 0);
  if (card.machines.length === 0) {
    return { finished, queued: 0, active: card.qty - finished };
  }
  let queued = 0;
  let active = 0;
  for (const [context, qty] of card.machines) {
    if (context === 'queue') queued += qty;
    else active += qty;
  }
  return { finished, queued, active };
}

/**
 * The portion of a transfer quantity that implicitly completes source
 * processing. A transfer consumes finished quantity first (already
 * completed — plain transfer), then queued quantity (never processed —
 * plain transfer), and only the remainder comes from actively
 * processing quantity, whose processing the transfer completes
 * (`AREA_COMPLETED` immediately before `TRANSFERRED`, one atomic
 * command).
 */
export function completionRequired(card: MockAreaCard, qty: number): number {
  const { finished, queued } = cardBreakdown(card);
  return Math.max(0, qty - finished - queued);
}

function findCard(
  cards: MockAreaCard[],
  pn: string,
  area: AreaKey,
): MockAreaCard | undefined {
  return cards.find((c) => pnKey(c.pn) === pnKey(pn) && c.area === area);
}

/** Merge `delta` pcs into the tuple named `context` (drop at zero). */
function adjustTuple(card: MockAreaCard, context: string, delta: number): void {
  const index = card.machines.findIndex(([name]) => name === context);
  if (index >= 0) {
    const next = card.machines[index][1] + delta;
    if (next > 0) card.machines[index] = [context, next];
    else card.machines.splice(index, 1);
  } else if (delta > 0) {
    card.machines.push([context, delta]);
  }
}

/** Merge finished quantity by completion context (drop at zero). */
function adjustFinished(
  card: MockAreaCard,
  completedBy: string | undefined,
  delta: number,
): void {
  const list = card.finished ?? [];
  const index = list.findIndex((f) => f.completedBy === completedBy);
  if (index >= 0) {
    const next = list[index].qty + delta;
    if (next > 0) list[index] = { ...list[index], qty: next };
    else list.splice(index, 1);
  } else if (delta > 0) {
    list.push(completedBy ? { qty: delta, completedBy } : { qty: delta });
  }
  card.finished = list.length ? list : undefined;
}

function dropEmpty(cards: MockAreaCard[]): MockAreaCard[] {
  return cards.filter((c) => c.qty > 0);
}

/**
 * Manual DONE: the selected quantity finishes processing at the Area.
 * The current Machine clears (the quantity leaves the Machine card);
 * the Area remains the physical location; the finished portion waits
 * on the rack as `READY_TO_TRANSFER`. Other quantity of the same PN is
 * untouched.
 */
export function applyDone(
  cards: MockAreaCard[],
  args: { pn: string; area: AreaKey; machine: string | null; qty: number },
): MockAreaCard[] {
  const card = findCard(cards, args.pn, args.area);
  if (!card) return cards;
  if (args.machine) {
    adjustTuple(card, args.machine, -args.qty);
  } else if (card.machines.some(([name]) => name === 'vendor')) {
    // Direct processing tracked as an outside-vendor portion.
    adjustTuple(card, 'vendor', -args.qty);
  }
  adjustFinished(card, args.machine ?? undefined, args.qty);
  return cards;
}

/** QUEUE return: unfinished quantity goes back to the Area queue. */
export function applyQueueReturn(
  cards: MockAreaCard[],
  args: { pn: string; area: AreaKey; machine: string; qty: number },
): MockAreaCard[] {
  const card = findCard(cards, args.pn, args.area);
  if (!card) return cards;
  adjustTuple(card, args.machine, -args.qty);
  adjustTuple(card, 'queue', args.qty);
  return cards;
}

/** One-shot assignment: queued quantity moves onto the Machine. */
export function applyAssign(
  cards: MockAreaCard[],
  args: { pn: string; area: AreaKey; machine: string; qty: number },
): MockAreaCard[] {
  const card = findCard(cards, args.pn, args.area);
  if (!card) return cards;
  adjustTuple(card, 'queue', -args.qty);
  adjustTuple(card, args.machine, args.qty);
  return cards;
}

/**
 * Consume `qty` from a source card for a transfer out: finished first,
 * then queued, then actively processing quantity (the portion the
 * transfer implicitly completes). The card disappears when empty;
 * remaining quantity keeps its existing state.
 */
function consumeForTransfer(card: MockAreaCard, qty: number): void {
  let remaining = qty;
  // Finished portions first.
  for (const portion of [...(card.finished ?? [])]) {
    if (remaining === 0) break;
    const take = Math.min(portion.qty, remaining);
    adjustFinished(card, portion.completedBy, -take);
    remaining -= take;
  }
  if (card.machines.length === 0) {
    // Direct processing: the remainder simply reduces the card total.
  } else {
    // Queue portions, then Machine/vendor portions.
    const order = [...card.machines].sort((a, b) => {
      const aq = isQueueContext(a[0]) ? 0 : 1;
      const bq = isQueueContext(b[0]) ? 0 : 1;
      return aq - bq;
    });
    for (const [context, available] of order) {
      if (remaining === 0) break;
      const take = Math.min(available, remaining);
      adjustTuple(card, context, -take);
      remaining -= take;
    }
  }
  card.qty -= qty;
}

/**
 * Transfer into the destination Area (one atomic command). The moved
 * quantity leaves the source position and enters the destination queue
 * (Areas with Machines) or direct processing (Areas without Machines).
 */
export function applyTransferIn(
  cards: MockAreaCard[],
  args: {
    pn: string;
    fromArea: AreaKey;
    toArea: AreaKey;
    qty: number;
    destinationHasMachines: boolean;
    destinationOperation: string;
  },
): MockAreaCard[] {
  const source = findCard(cards, args.pn, args.fromArea);
  if (!source) return cards;
  consumeForTransfer(source, args.qty);
  const dest = findCard(cards, args.pn, args.toArea);
  if (dest) {
    dest.qty += args.qty;
    if (dest.machines.length > 0 || args.destinationHasMachines) {
      adjustTuple(dest, 'queue', args.qty);
    }
  } else {
    cards.push({
      area: args.toArea,
      pn: source.pn,
      workOrder: `${source.workOrder.split(' ·')[0]} · ${args.destinationOperation}`,
      job: source.job,
      qty: args.qty,
      machines: args.destinationHasMachines ? [['queue', args.qty]] : [],
      due: source.due,
      dueClass: source.dueClass,
      timeInArea: '0m',
      hotRank: source.hotRank,
      dueDays: source.dueDays,
      timeInAreaMinutes: 0,
      received: source.received,
    });
  }
  return dropEmpty(cards);
}

/**
 * Introduce quantity at the Area (intake or auditable addition): the
 * new quantity enters the Area queue or direct processing.
 */
export function applyIntroduce(
  cards: MockAreaCard[],
  args: {
    pn: string;
    area: AreaKey;
    qty: number;
    hasMachines: boolean;
    /** Row labels for a card created by an intake of a new PN. */
    workOrder: string;
    job: string;
    due: string;
    received: string;
  },
): MockAreaCard[] {
  const card = findCard(cards, args.pn, args.area);
  if (card) {
    card.qty += args.qty;
    if (args.hasMachines) adjustTuple(card, 'queue', args.qty);
    return cards;
  }
  cards.push({
    area: args.area,
    pn: args.pn,
    workOrder: args.workOrder,
    job: args.job,
    qty: args.qty,
    machines: args.hasMachines ? [['queue', args.qty]] : [],
    due: args.due,
    dueClass: args.due === 'No due date' ? 'none' : 'ok',
    timeInArea: '0m',
    dueDays: null,
    timeInAreaMinutes: 0,
    received: args.received,
  });
  return cards;
}

/** Scrap removes damaged quantity from active production. */
export function applyScrap(
  cards: MockAreaCard[],
  args: { pn: string; area: AreaKey; qty: number },
): MockAreaCard[] {
  const card = findCard(cards, args.pn, args.area);
  if (!card) return cards;
  consumeForTransfer(card, args.qty);
  card.scrapped = (card.scrapped ?? 0) + args.qty;
  // A fully scrapped presence keeps its card so the scrap stays visible.
  return cards;
}
