// Development-only long-data preview of the Area Board (`?state=long`).
//
// A deterministic board with an over-long Part Number, an over-long Job
// Number, many rows in one Area, every holding state, an Area without
// Machines and the terminal Stockroom — enough to verify the shared row
// truncation, the grouping and the column layout without a busy
// Department. Authored as relative offsets resolved once at module
// load, in the API model the real feed delivers.
//
// `import.meta.env.DEV` is replaced statically by Vite, so the whole
// fixture is dead code in a production build and never ships (verified
// by src/production-boundary.test.ts). It is NOT a mock view: the real
// view reads the real feed in every build.

import type { AreaBoard, AreaBoardArea } from '../../api/area-board';
import type {
  AreaInventory,
  FlowInArea,
  InventoryLine,
  MachineRef,
} from '../../api/area-inventory';

const minutesAgoIso = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

const isoDateIn = (days: number) => {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
};

interface PreviewFlow {
  id: number;
  pn: string;
  qty: number;
  state: FlowInArea['processingState'];
  machineId?: number;
  completedMachineId?: number;
  minutesInArea: number;
  woNumber: string | null;
  job: string;
  dueInDays: number | null;
  hotRank?: number;
}

function flow(item: PreviewFlow, operationName: string): FlowInArea {
  return {
    partNumber: item.pn,
    quantityFlowId: item.id,
    quantity: item.qty,
    routeMode: 'FLOATING',
    operation: {
      id: 1,
      code: operationName.toUpperCase().slice(0, 4),
      name: operationName,
      isExternal: false,
      isActive: true,
    },
    processingState: item.state,
    machineId: item.machineId ?? null,
    completedMachineId: item.completedMachineId ?? null,
    enteredAt: minutesAgoIso(item.minutesInArea),
    availableActions: ['TRANSFER', 'SCRAP'],
    workOrder: {
      workOrderId: item.id,
      workOrderNumber: item.woNumber,
      workOrderDemandId: item.id,
      requestType: 'NEW',
      jobNumbers: [item.job],
      dueDate: item.dueInDays === null ? null : isoDateIn(item.dueInDays),
      priorityRank: item.hotRank ?? null,
      receivedDate: isoDateIn(-24),
    },
  };
}

function lines(flows: FlowInArea[]): InventoryLine[] {
  const byPn = new Map<string, FlowInArea[]>();
  for (const item of flows) {
    byPn.set(item.partNumber, [...(byPn.get(item.partNumber) ?? []), item]);
  }
  return [...byPn].map(([partNumber, group]) => ({
    partNumber,
    totalQuantity: group.reduce((sum, item) => sum + item.quantity, 0),
    flows: group,
  }));
}

function machine(id: number, name: string, running: boolean): MachineRef {
  return {
    id,
    name,
    assetTag: `PB-${1000 + id}`,
    barcodeValue: `PF:MACHINE:PB-${1000 + id}`,
    operationalState: running ? 'RUNNING' : 'IDLE',
    stateChangedAt: minutesAgoIso(90 + id * 17),
    maintenanceSince: null,
    maintenanceNote: null,
    maintenanceExpectedReturn: null,
  };
}

function inventory(
  area: AreaInventory['area'],
  flows: FlowInArea[],
  machines: MachineRef[],
): AreaInventory {
  const byState = (state: FlowInArea['processingState']) =>
    lines(flows.filter((item) => item.processingState === state));
  const total = (state: FlowInArea['processingState']) =>
    flows
      .filter((item) => item.processingState === state)
      .reduce((sum, item) => sum + item.quantity, 0);
  return {
    area,
    hasMachines: machines.length > 0,
    lines: lines(flows),
    totalPartNumbers: lines(flows).length,
    totalQuantity: flows.reduce((sum, item) => sum + item.quantity, 0),
    queued: byState('QUEUED'),
    queuedQuantity: total('QUEUED'),
    machines: machines.map((item) => {
      const held = flows.filter(
        (entry) =>
          entry.processingState === 'ON_MACHINE' && entry.machineId === item.id,
      );
      return {
        machine: item,
        lines: lines(held),
        totalQuantity: held.reduce((sum, entry) => sum + entry.quantity, 0),
      };
    }),
    onMachineQuantity: total('ON_MACHINE'),
    processing: byState('PROCESSING'),
    processingQuantity: total('PROCESSING'),
    finished: byState('READY_TO_TRANSFER'),
    finishedQuantity: total('READY_TO_TRANSFER'),
  };
}

const LONG_PN = '0118-40-0022-07-0455-88-REV-C';

function longPreviewBoard(): AreaBoard {
  const lathe = {
    id: 2,
    name: 'Lathe',
    color: 'var(--a-lathe)',
    description: 'Turning cell · Lathe 1–4',
    isTerminal: false,
  };
  const deburr = {
    id: 5,
    name: 'Deburr',
    color: 'var(--a-deburr)',
    description: 'Manual finishing',
    isTerminal: false,
  };
  const stockroom = {
    id: 8,
    name: 'Stockroom',
    color: 'var(--a-stockroom)',
    description: 'Finished goods',
    isTerminal: true,
  };
  const machines = [
    machine(1, 'Lathe 1', true),
    machine(2, 'Lathe 2', true),
    machine(3, 'Lathe 3 — Horizontal Boring Mill', false),
  ];
  const latheFlows: FlowInArea[] = [
    flow(
      {
        id: 1,
        pn: LONG_PN,
        qty: 14,
        state: 'ON_MACHINE',
        machineId: 1,
        minutesInArea: 585,
        woNumber: '007042',
        job: '19311-CUSTOMER-REFERENCE-00098',
        dueInDays: 4,
        hotRank: 1,
      },
      'Turning',
    ),
    flow(
      {
        id: 2,
        pn: '2027-60-8114-00',
        qty: 6,
        state: 'ON_MACHINE',
        machineId: 2,
        minutesInArea: 220,
        woNumber: '007001',
        job: '18112',
        dueInDays: 2,
      },
      'Turning',
    ),
    flow(
      {
        id: 3,
        pn: '2027-60-8114-00',
        qty: 2,
        state: 'READY_TO_TRANSFER',
        completedMachineId: 2,
        minutesInArea: 45,
        woNumber: '007001',
        job: '18112',
        dueInDays: 2,
      },
      'Turning',
    ),
    ...Array.from({ length: 14 }, (_, index) => {
      const n = index + 1;
      return flow(
        {
          id: 100 + n,
          pn: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
          qty: (n % 6) + 1,
          state: 'QUEUED',
          minutesInArea: ((n % 8) + 1) * 60 + 10,
          woNumber: n % 4 === 0 ? null : String(7200 + n).padStart(6, '0'),
          job: String(19000 + n),
          dueInDays: n % 5 === 0 ? null : (n % 15) + 3,
        },
        'Turning',
      );
    }),
  ];
  const deburrFlows: FlowInArea[] = [
    flow(
      {
        id: 60,
        pn: '81-1042',
        qty: 6,
        state: 'PROCESSING',
        minutesInArea: 140,
        woNumber: '007021',
        job: '18615',
        dueInDays: 7,
      },
      'Deburring',
    ),
    flow(
      {
        id: 61,
        pn: '78-04-0031',
        qty: 3,
        state: 'READY_TO_TRANSFER',
        minutesInArea: 30,
        woNumber: '007002',
        job: '18102',
        dueInDays: 16,
      },
      'Deburring',
    ),
  ];
  const areas: AreaBoardArea[] = [
    {
      inventory: inventory(deburr, deburrFlows, []),
      operations: [
        { id: 5, code: 'DEB', name: 'Deburring', isExternal: false },
      ],
      scrapped: {},
      stocked: [],
    },
    {
      inventory: inventory(lathe, latheFlows, machines),
      operations: [{ id: 1, code: 'TURN', name: 'Turning', isExternal: false }],
      scrapped: { '2027-60-8114-00': 1 },
      stocked: [],
    },
    {
      inventory: inventory(stockroom, [], []),
      operations: [{ id: 9, code: 'STK', name: 'Stocking', isExternal: false }],
      scrapped: {},
      stocked: [
        { partNumber: '309-127', quantity: 50, allocatedQuantity: 50 },
        { partNumber: '142-260', quantity: 18, allocatedQuantity: 4 },
      ],
    },
  ];
  return { department: { id: 1, name: 'Machining' }, areas };
}

export const LONG_PREVIEW_BOARD: AreaBoard | null = import.meta.env.DEV
  ? longPreviewBoard()
  : null;
