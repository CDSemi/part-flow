// The shared mapping from the server's Area monitoring model to the
// shared row presentation (GUI_DESIGN §4.10, §6.3; PROJECT_PROFILE §21).
//
// Both surfaces that render an Area — the Scan Station's `In this Area
// now` and the Area Board (its per-Area detail and its All Areas
// overview) — turn the SAME read (`api/area-inventory`) into the SAME
// presentation shapes here, so a quantity cannot be described
// differently by the two views. The components then render those
// shapes identically; only the action rail differs, and the Area Board
// passes none.
//
// Presentation mapping only: no business rules and no derived time
// values. Every state, entry timestamp, Machine attribution and demand
// context is the server's; the displayed `Time in Area` and due
// countdown are derived at render from the fixed values kept here.
//
// Production-safe: no mock data, no framework imports.

import type {
  AreaInventory,
  AreaRef,
  DemandContext,
  FlowInArea,
  MachineRef,
  OperationRef,
} from '../api/area-inventory';
import { areaRefColor } from '../api/area-inventory';
import type { MockArea, MockAreaCard, MockAreaMachine } from './view-models';

/** Operator-facing Operation label: the name when configured, else the code. */
export function operationLabel(operation: {
  code: string;
  name: string | null;
}): string {
  return operation.name ?? operation.code;
}

/**
 * The shared Area/Machine monitoring components take the presentation
 * shapes of `views/view-models` (their `key`/`area` fields carry the
 * development registry's Area keys, which the components never read —
 * they only use name, color, description, Operations and terminal
 * flag). A real Area is identified by its server id; the key below is
 * a stable render identity only.
 */
export function presentationArea(
  area: AreaRef,
  operations: readonly OperationRef[],
): MockArea {
  return {
    key: areaKey(area.id),
    name: area.name,
    colorVar: areaRefColor(area),
    description: area.description ?? '',
    operations: operations.map(operationLabel),
    terminal: area.isTerminal,
  };
}

/** Stable render identity of one real Area. */
export function areaKey(areaId: number): MockArea['key'] {
  return `area-${areaId}` as MockArea['key'];
}

/**
 * Work Order label of one demand, in the shared card label form. A PN
 * with no open demand reads `WO —`: monitoring says what the quantity
 * is worked FOR, and "nothing open" is an honest answer — never the
 * completed Work Order the quantity happens to descend from.
 */
export function workOrderLabel(
  demand:
    { workOrderNumber: string | null; requestType: string } | null | undefined,
): string {
  if (!demand) return 'WO —';
  return `WO ${demand.workOrderNumber ?? '—'} · ${demand.requestType}`;
}

/** Job Number line of one demand (`—` when it names none). */
export function jobNumbersLabel(demand: DemandContext | undefined): string {
  if (!demand || demand.jobNumbers.length === 0) return '—';
  return demand.jobNumbers.join(', ');
}

/**
 * The full monitoring context of a PN, spelled out for the row's
 * tooltip: every OPEN demand in the canonical order, so several
 * demands are never hidden behind the defining one.
 */
export function demandsTitle(demands: readonly DemandContext[]): string {
  if (demands.length === 0) return 'No open Work Order Demand';
  return demands
    .map(
      (demand) =>
        `WO ${demand.workOrderNumber ?? '—'} · ${jobNumbersLabel(demand)} · ${
          demand.requestedQuantity
        } pcs`,
    )
    .join('\n');
}

/**
 * The searchable text of a PN's whole monitoring context: EVERY open
 * demand's Work Order Number and Job Numbers, so a search for a demand
 * the row does not name — one of its `+N more` — still finds it.
 */
export function demandsSearchText(demands: readonly DemandContext[]): string {
  return demands
    .flatMap((demand) => [demand.workOrderNumber ?? '', ...demand.jobNumbers])
    .join(' ');
}

/**
 * The monitoring values a row takes from its PN's OPEN demands: the
 * FIRST demand in the canonical order defines the Hot rank, the due
 * date and the received date the countdown policy uses, while the rest
 * stay counted so the row can say there are more — and searchable, so
 * none of them is reachable only through the tooltip.
 */
function monitoringContext(demands: readonly DemandContext[]) {
  const defining = demands[0];
  return {
    workOrder: workOrderLabel(defining),
    job: jobNumbersLabel(defining),
    due: defining?.dueDate ?? null,
    received: defining?.receivedDate ?? '',
    ...(defining?.priorityRank !== null && defining?.priorityRank !== undefined
      ? { hotRank: defining.priorityRank }
      : {}),
    ...(demands.length > 1 ? { moreDemands: demands.length - 1 } : {}),
    demandsTitle: demandsTitle(demands),
    ...(demands.length > 0
      ? { demandsSearch: demandsSearchText(demands) }
      : {}),
  };
}

export interface AreaInventoryPresentation {
  cards: MockAreaCard[];
  machines: MockAreaMachine[];
  machineByName: Map<string, MachineRef>;
  /** A rendered row back to its flow, so a row action knows exactly
   * which Quantity Flow it acts on (Scan Station only). */
  flowOf: Map<MockAreaCard, FlowInArea>;
}

const MACHINE_CARD_STATUS: Record<
  MachineRef['operationalState'],
  MockAreaMachine['status']
> = { RUNNING: 'running', IDLE: 'idle', MAINTENANCE: 'maintenance' };

/**
 * The presentation of one Area inventory: one presence card per
 * Quantity Flow in the Area, its portion placed by the server's derived
 * processing state — the Area queue (`queue` context), the Machine it
 * is on (the Machine's name — the shared grouping puts it on that
 * Machine's card), the direct processing of an Area without Machines
 * (`PROCESSING` — a direct portion, or the `vendor` context when the
 * flow's Operation is an external one, so the row reads `External
 * processing`), or the finished rack (`READY_TO_TRANSFER`, Area summary
 * only, naming the Machine that completed the work as context).
 *
 * Each card carries the fixed monitoring values of its quantity — the
 * Area-entry timestamp from the flow, and the due date, Job Numbers
 * and Hot rank of the PN's OPEN demands (never of the demand the
 * quantity descends from) — and, when the caller supplies it, the PN's
 * scrapped quantity in this Area.
 */
export function presentAreaInventory(
  inventory: AreaInventory,
  options: { scrapped?: Readonly<Record<string, number>> } = {},
): AreaInventoryPresentation {
  // The Area mode is the inventory's own — the SERVER's judgement from
  // the Area's active Machines at the moment this inventory was read
  // (PROJECT_PROFILE §12), consistent with the flow states it carries.
  const hasMachines = inventory.hasMachines;
  const key = areaKey(inventory.area.id);
  const machineByName = new Map<string, MachineRef>();
  const machineById = new Map<number, MachineRef>();
  for (const card of inventory.machines) {
    machineByName.set(card.machine.name, card.machine);
    machineById.set(card.machine.id, card.machine);
  }
  const cards: MockAreaCard[] = [];
  const flowOf = new Map<MockAreaCard, FlowInArea>();
  for (const line of inventory.lines) {
    for (const flow of line.flows) {
      const onMachine =
        flow.processingState === 'ON_MACHINE' && flow.machineId !== null
          ? machineById.get(flow.machineId)
          : undefined;
      // Direct processing at an external Operation (an outside vendor)
      // is the `vendor` portion of the shared presentation — decided by
      // the flow's RECORDED Operation (active or not), never by the
      // Area's name or the station's active Operations.
      const external =
        flow.processingState === 'PROCESSING' && flow.operation.isExternal;
      // The completing Machine comes from the flow itself, never from
      // the Area's active cards: a Machine retired after finishing the
      // work is no longer a card and must still name the completion.
      const completedBy = flow.completedMachine?.name;
      const scrapped = options.scrapped?.[flow.partNumber];
      const card: MockAreaCard = {
        area: key,
        pn: flow.partNumber,
        // The monitoring line of EVERY shared row — the Scan Station's
        // and the Area Board's alike — is what the PN is worked FOR.
        // The demand this quantity descends from is workflow and audit
        // context: it belongs to the action dialogs and recaps, and
        // never to a monitoring row (`flow.workOrder`).
        ...monitoringContext(inventory.demandContext[flow.partNumber] ?? []),
        qty: flow.quantity,
        machines: !hasMachines
          ? external
            ? [['vendor', flow.quantity]]
            : []
          : onMachine
            ? [[onMachine.name, flow.quantity]]
            : flow.processingState === 'QUEUED'
              ? [['queue', flow.quantity]]
              : [],
        finished:
          flow.processingState === 'READY_TO_TRANSFER'
            ? [{ qty: flow.quantity, ...(completedBy ? { completedBy } : {}) }]
            : undefined,
        enteredAreaAt: flow.enteredAt,
        ...(scrapped ? { scrapped } : {}),
      };
      cards.push(card);
      flowOf.set(card, flow);
    }
  }
  return {
    cards,
    machines: inventory.machines.map(({ machine }) => ({
      name: machine.name,
      status: MACHINE_CARD_STATUS[machine.operationalState],
      stateChangedAt: machine.stateChangedAt,
      maintenanceNote: machine.maintenanceNote ?? undefined,
      expectedReturn: machine.maintenanceExpectedReturn ?? undefined,
    })),
    machineByName,
    flowOf,
  };
}

export const EMPTY_AREA_PRESENTATION: AreaInventoryPresentation = {
  cards: [],
  machines: [],
  machineByName: new Map(),
  flowOf: new Map(),
};

/**
 * The All Areas overview is PN-centric (GUI_DESIGN §6.2): one row per
 * Part Number in an Area, whatever number of separate quantities the
 * PN holds there. The portions of every quantity are aggregated into
 * that one row — `Lathe 3 × 3`, `queue × 2`, `done × 1` — so the PN is
 * counted once and its pieces are neither lost nor counted twice; the
 * per-Area DETAIL keeps one row per quantity, because each is acted on
 * separately at the Scan Station.
 *
 * Rows keep the order of their first card, so the toolbar's search and
 * sort still decide the column order. The aggregated row is dated from
 * the OLDEST portion — the longest wait is what a monitoring view must
 * not hide — and every other value is the PN's own (its monitoring
 * context and its scrapped quantity are PN-level already).
 */
export function aggregateByPartNumber(
  cards: readonly MockAreaCard[],
): MockAreaCard[] {
  const rows = new Map<string, MockAreaCard>();
  for (const card of cards) {
    const merged = rows.get(card.pn);
    if (merged === undefined) {
      rows.set(card.pn, {
        ...card,
        machines: card.machines.map(
          (portion) => [...portion] as [string, number],
        ),
        ...(card.finished
          ? { finished: card.finished.map((part) => ({ ...part })) }
          : {}),
      });
      continue;
    }
    merged.qty += card.qty;
    for (const [context, qty] of card.machines) {
      const existing = merged.machines.find(
        (portion) => portion[0] === context,
      );
      if (existing) existing[1] += qty;
      else merged.machines.push([context, qty]);
    }
    for (const part of card.finished ?? []) {
      merged.finished ??= [];
      const existing = merged.finished.find(
        (entry) => entry.completedBy === part.completedBy,
      );
      if (existing) existing.qty += part.qty;
      else merged.finished.push({ ...part });
    }
    if (
      card.enteredAreaAt !== null &&
      (merged.enteredAreaAt === null ||
        card.enteredAreaAt < merged.enteredAreaAt)
    ) {
      merged.enteredAreaAt = card.enteredAreaAt;
    }
  }
  return [...rows.values()];
}
