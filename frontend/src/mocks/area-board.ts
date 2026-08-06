import { machineAssignedQty, toAreaMachine } from '../views/machine-state';
import type {
  AreaKey,
  MockAreaCard,
  MockAreaMachine,
} from '../views/view-models';
import { activeMachines } from './machines';
import { isoDateIn, minutesAgoIso } from './mock-time';

// `due: null` marks a WO Demand without a due date (valid data);
// `received` (parent WO received date) orders undated demands.
// Due dates and Area-entry timestamps are authored as relative offsets
// (mock-time.ts) resolved once at load into fixed data; the views
// derive the countdown / `Time in Area` display from them through the
// shared UI clock — formatted durations are never stored.
export const MOCK_AREA_CARDS: MockAreaCard[] = [
  {
    area: 'material',
    pn: '0455-20-0118-03',
    workOrder: 'WO 007003 · Receiving',
    job: '18190',
    qty: 8,
    machines: [],
    due: isoDateIn(9),
    enteredAreaAt: minutesAgoIso(2941),
    received: '2026-07-12',
  },
  {
    area: 'cut',
    pn: '2027-60-8114-00',
    workOrder: 'WO 007001 · Cutting',
    job: '18112',
    qty: 4,
    machines: [['Saw 1', 4]],
    due: isoDateIn(2),
    enteredAreaAt: minutesAgoIso(220),
    hotRank: 1,
    received: '2026-07-12',
  },
  {
    // Partially on a Machine and partially DONE: 3 pcs still turning on
    // Lathe 3, 2 pcs queued, 1 pc finished (completed by Lathe 3) and
    // waiting on the finished rack — READY_TO_TRANSFER, current
    // Machine cleared, Area unchanged.
    area: 'lathe',
    pn: '2027-60-8114-00',
    workOrder: 'WO 007001 · Turning',
    job: '18112',
    qty: 6,
    machines: [
      ['Lathe 3', 3],
      ['queue', 2],
    ],
    finished: [{ qty: 1, completedBy: 'Lathe 3' }],
    due: isoDateIn(2),
    enteredAreaAt: minutesAgoIso(125),
    hotRank: 1,
    received: '2026-07-12',
    scrapped: 1,
  },
  {
    area: 'lathe',
    pn: '0455-20-0118-03',
    workOrder: 'WO 007003 · Turning',
    job: '18190',
    qty: 4,
    machines: [['Lathe 2', 4]],
    due: isoDateIn(9),
    enteredAreaAt: minutesAgoIso(65),
    received: '2026-07-12',
  },
  {
    // Internal MODIFY demand without an external Work Order Number:
    // the blank number displays as `—` (display-only placeholder).
    area: 'lathe',
    pn: '214-406',
    workOrder: 'WO — · Turning · MODIFY',
    job: '— (internal)',
    qty: 2,
    machines: [['queue', 2]],
    due: isoDateIn(-1),
    enteredAreaAt: minutesAgoIso(372),
    received: '2026-07-21',
    scrapped: 1,
  },
  {
    area: 'mill',
    pn: '78-04-0031',
    workOrder: 'WO 007002 · Milling',
    job: '18102',
    qty: 3,
    machines: [['Mill 1', 3]],
    due: isoDateIn(16),
    enteredAreaAt: minutesAgoIso(45),
    received: '2026-07-14',
  },
  {
    // Long-PN reference case from GUI_DESIGN §6.3: the qty block stays
    // anchored right; the PN truncates with an ellipsis + title tooltip.
    area: 'mill',
    pn: '0118-40-0022-07-0455-88-REV-C',
    workOrder: 'WO 007008 · Milling',
    job: '18455',
    qty: 2,
    machines: [['Mill 2', 2]],
    due: isoDateIn(12),
    enteredAreaAt: minutesAgoIso(80),
    received: '2026-07-04',
  },
  {
    // WO Demand without a due date — displayed as `No due date` and
    // ordered after all dated demands by the WO received date.
    area: 'manual',
    pn: '118-052',
    workOrder: 'WO 007011 · Manual work',
    job: '18520',
    qty: 4,
    machines: [],
    due: null,
    enteredAreaAt: minutesAgoIso(320),
    received: '2026-07-19',
  },
  {
    // DONE in an Area without Machines: deburring finished for the
    // whole quantity — READY_TO_TRANSFER on the finished rack. A
    // transfer from this source appends only TRANSFERRED (processing
    // was already completed).
    area: 'deburr',
    pn: '78-04-0031',
    workOrder: 'WO 007002 · Deburring',
    job: '18102',
    qty: 3,
    machines: [],
    finished: [{ qty: 3 }],
    due: isoDateIn(16),
    enteredAreaAt: minutesAgoIso(30),
    received: '2026-07-14',
  },
  {
    // Actively processing quantity in an Area WITHOUT Machines (direct
    // processing): at the Scan Station exactly this row carries the
    // DONE action — the direct-processing exception to the no-row-
    // actions rule (GUI_DESIGN §4.10); partial DONE moves only the
    // confirmed portion to the finished rack.
    area: 'deburr',
    pn: '81-1042',
    workOrder: 'WO 007021 · Deburring',
    job: '18615',
    qty: 6,
    machines: [],
    due: isoDateIn(7),
    enteredAreaAt: minutesAgoIso(140),
    received: '2026-07-22',
  },
  {
    area: 'external',
    pn: '142-260',
    workOrder: 'WO 007005 · Plating',
    job: '18031',
    qty: 20,
    machines: [['vendor', 20]],
    due: isoDateIn(-6),
    enteredAreaAt: minutesAgoIso(5880),
    hotRank: 2,
    received: '2026-07-06',
    scrapped: 2,
  },
  {
    area: 'stockroom',
    pn: '309-127',
    workOrder: 'WO 006996 · stocked',
    job: '17740',
    qty: 50,
    machines: [],
    due: null,
    dueText: 'allocated 50/50',
    enteredAreaAt: null,
    received: '2026-06-18',
  },
];

// Machines belonging to each Area — shared by the Area Board detail and
// the Scan Station monitoring layout, projected from the Machine
// registry (mocks/machines.ts): retired Machines never appear, and the
// running/idle states are DERIVED from the assigned quantity above
// (maintenance stays an explicit override). Areas without Machines
// (Material, Manual, Deburr, External, Stockroom) render only the
// full-width Area summary card — no placeholder Machine cards, no
// queue statistics. `Mill 3 — Horizontal Boring` stays the long-name
// reference case: it truncates where constrained, never pushing
// quantities or times out of alignment.
export const MOCK_AREA_MACHINES: Partial<Record<AreaKey, MockAreaMachine[]>> =
  activeMachines().reduce<Partial<Record<AreaKey, MockAreaMachine[]>>>(
    (byArea, machine) => {
      const projected = toAreaMachine(
        machine,
        machineAssignedQty(MOCK_AREA_CARDS, machine),
      );
      (byArea[machine.area] ??= []).push(projected);
      return byArea;
    },
    {},
  );

// Long-data preview: over-long PNs and many cards in one Area.
export const MOCK_AREA_CARDS_LONG: MockAreaCard[] = [
  ...MOCK_AREA_CARDS,
  {
    area: 'lathe',
    pn: '2199-60-9912-05-2027-11-REV-F',
    workOrder: 'WO 007042 · Turning',
    job: '19311-CUSTOMER-REF-00098',
    qty: 14,
    machines: [
      ['Lathe 1', 6],
      ['Lathe 4', 4],
      ['queue', 4],
    ],
    due: isoDateIn(4),
    enteredAreaAt: minutesAgoIso(585),
    received: '2026-07-10',
  },
  ...Array.from({ length: 14 }, (_, i): MockAreaCard => {
    const n = i + 1;
    return {
      area: 'lathe',
      pn: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      workOrder: `WO ${String(7200 + n).padStart(6, '0')} · Turning`,
      job: String(19000 + n),
      qty: (n % 6) + 1,
      machines: [['queue', (n % 6) + 1]],
      due: n % 5 === 0 ? null : isoDateIn((n % 15) + 3),
      enteredAreaAt: minutesAgoIso(((n % 8) + 1) * 60 + 10),
      received: `2026-07-${String((n % 20) + 1).padStart(2, '0')}`,
    };
  }),
];
