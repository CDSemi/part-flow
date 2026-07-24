import type {
  MockInventoryRow,
  MockMachineTile,
  MockScanRecord,
} from './types';

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
  { name: 'Lathe 2', note: 'running · SHAFT-014 × 4' },
  { name: 'Lathe 3', note: 'running · BRACKET-001 × 4' },
  { name: 'Lathe 4', note: 'maintenance', maintenance: true },
];

export const MOCK_RECENT_SCANS: MockScanRecord[] = [
  {
    pn: 'PF-BRACKET-001',
    description: 'ASSIGNED_TO_MACHINE · Lathe queue → Lathe 3 · qty 4',
    time: '13:05',
  },
  {
    pn: 'PF-BRACKET-001',
    description: 'TRANSFERRED · Cut → Lathe (queue) · qty 6',
    time: '11:20',
  },
  {
    pn: 'PF-SHAFT-014',
    description: 'ASSIGNED_TO_MACHINE · Lathe queue → Lathe 2 · qty 4',
    time: '09:12',
  },
];

export const MOCK_INVENTORY_ASSIGNED: MockInventoryRow[] = [
  { pn: 'PF-BRACKET-001', where: 'Lathe 3', qty: 4 },
  { pn: 'PF-SHAFT-014', where: 'Lathe 2', qty: 4 },
];

export const MOCK_INVENTORY_QUEUED: MockInventoryRow[] = [
  { pn: 'PF-BRACKET-001', where: 'queued', qty: 2 },
];

export const MOCK_INVENTORY_QUEUED_LONG: MockInventoryRow[] = [
  { pn: 'PF-BRACKET-001', where: 'queued', qty: 2 },
  { pn: 'PF-MANIFOLD-ASSY-00847-REV-C', where: 'queued', qty: 6 },
  ...Array.from({ length: 12 }, (_, i): MockInventoryRow => {
    const n = i + 1;
    return {
      pn: `PF-LONGRUN-${String(n).padStart(3, '0')}`,
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
    pn: 'PF-SHAFT-014',
    description: 'Drive shaft Ø25 · single valid context',
  },
  'PF:PN:1001': { kind: 'pn-ambiguous', pn: 'PF-BRACKET-001' },
  'PF:MACHINE:L1': { kind: 'machine', machine: 'Lathe 1' },
  'PF:MACHINE:L2': { kind: 'machine', machine: 'Lathe 2' },
  'PF:MACHINE:L3': { kind: 'machine', machine: 'Lathe 3' },
  'PF:MACHINE:L4': { kind: 'machine-inactive', machine: 'Lathe 4' },
  'PF:WORKER:88': { kind: 'worker', worker: 'V. Tran' },
  'PF:ACTION:REWORK': { kind: 'action', action: 'REWORK' },
  'PF:ACTION:MODIFY': { kind: 'action', action: 'MODIFY' },
};
