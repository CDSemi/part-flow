import type {
  MockInventoryRow,
  MockMachineTile,
  MockScanRecord,
} from '../views/view-models';

// Scan Station mock configuration: one station fixed to the Lathe Area.
export const MOCK_STATION = {
  department: 'Machine Shop',
  areaName: 'Lathe',
  areaColorVar: 'var(--a-lathe)',
  operations: 'Turning',
  stationId: 'LATHE-ST-01',
  worker: 'H. Nguyen',
  workerNote: 'expires 18:00 · scan badge to switch',
};

export const MOCK_MACHINES: MockMachineTile[] = [
  { name: 'Lathe 1', note: 'idle' },
  { name: 'Lathe 2', note: 'running · 0455-20-0118-03 × 4' },
  { name: 'Lathe 3', note: 'running · 2027-60-8114-00 × 4' },
  { name: 'Lathe 4', note: 'maintenance', maintenance: true },
];

export const MOCK_RECENT_SCANS: MockScanRecord[] = [
  {
    pn: '2027-60-8114-00',
    description: 'ASSIGNED_TO_MACHINE · Lathe queue → Lathe 3 · qty 4',
    time: '13:05',
  },
  {
    pn: '2027-60-8114-00',
    description: 'TRANSFERRED · Cut → Lathe (queue) · qty 6',
    time: '11:20',
  },
  {
    pn: '0455-20-0118-03',
    description: 'ASSIGNED_TO_MACHINE · Lathe queue → Lathe 2 · qty 4',
    time: '09:12',
  },
];

export const MOCK_INVENTORY_ASSIGNED: MockInventoryRow[] = [
  { pn: '2027-60-8114-00', where: 'Lathe 3', qty: 4 },
  { pn: '0455-20-0118-03', where: 'Lathe 2', qty: 4 },
];

export const MOCK_INVENTORY_QUEUED: MockInventoryRow[] = [
  { pn: '2027-60-8114-00', where: 'queued', qty: 2 },
];

export const MOCK_INVENTORY_QUEUED_LONG: MockInventoryRow[] = [
  { pn: '2027-60-8114-00', where: 'queued', qty: 2 },
  { pn: '0118-40-0022-07-0455-88-REV-C', where: 'queued', qty: 6 },
  ...Array.from({ length: 12 }, (_, i): MockInventoryRow => {
    const n = i + 1;
    return {
      pn: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      where: 'queued',
      qty: (n % 5) + 1,
    };
  }),
];

// Mock barcode resolution table. This demonstrates the approved feedback
// surfaces only — real barcode resolution is server-side (Phase 5+).
export const MOCK_SCAN_OUTCOMES: Record<
  string,
  | { kind: 'pn-single'; pn: string; description: string }
  | { kind: 'pn-ambiguous'; pn: string }
  | { kind: 'machine'; machine: string }
  | { kind: 'machine-inactive'; machine: string }
  | { kind: 'worker'; worker: string }
  | { kind: 'action'; action: 'REWORK' | 'MODIFY' }
> = {
  'PF:PN:1014': {
    kind: 'pn-single',
    pn: '0455-20-0118-03',
    description: 'SHAFT, DRIVE 0.750 DIA X 12.500 · single valid context',
  },
  'PF:PN:1001': { kind: 'pn-ambiguous', pn: '2027-60-8114-00' },
  'PF:MACHINE:L1': { kind: 'machine', machine: 'Lathe 1' },
  'PF:MACHINE:L2': { kind: 'machine', machine: 'Lathe 2' },
  'PF:MACHINE:L3': { kind: 'machine', machine: 'Lathe 3' },
  'PF:MACHINE:L4': { kind: 'machine-inactive', machine: 'Lathe 4' },
  'PF:WORKER:88': { kind: 'worker', worker: 'V. Tran' },
  'PF:ACTION:REWORK': { kind: 'action', action: 'REWORK' },
  'PF:ACTION:MODIFY': { kind: 'action', action: 'MODIFY' },
};
