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
  FlowInArea,
  MachineRef,
  OperationRef,
  WorkOrderContext,
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

/** Work Order context line of a flow, in the shared card label form. */
export function workOrderLabel(workOrder: WorkOrderContext | null): string {
  if (!workOrder) return 'WO —';
  return `WO ${workOrder.workOrderNumber ?? '—'} · ${workOrder.requestType}`;
}

/** Job Number line of a flow (`—` when the demand names none). */
export function jobNumbersLabel(workOrder: WorkOrderContext | null): string {
  if (!workOrder || workOrder.jobNumbers.length === 0) return '—';
  return workOrder.jobNumbers.join(', ');
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
 * Each card carries the fixed monitoring values of its flow — the
 * Area-entry timestamp, the demand's due date, Job Numbers, Hot rank
 * and received date — and, when the caller supplies it, the PN's
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
      const completedBy =
        flow.completedMachineId !== null
          ? machineById.get(flow.completedMachineId)?.name
          : undefined;
      const scrapped = options.scrapped?.[flow.partNumber];
      const card: MockAreaCard = {
        area: key,
        pn: flow.partNumber,
        workOrder: workOrderLabel(flow.workOrder),
        job: jobNumbersLabel(flow.workOrder),
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
        due: flow.workOrder?.dueDate ?? null,
        enteredAreaAt: flow.enteredAt,
        ...(flow.workOrder?.priorityRank !== null &&
        flow.workOrder?.priorityRank !== undefined
          ? { hotRank: flow.workOrder.priorityRank }
          : {}),
        received: flow.workOrder?.receivedDate ?? '',
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
