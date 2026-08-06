import type { MockScanStation } from '../views/view-models';
import { minutesAgoIso } from './mock-time';

// Scan Station mock registry: the station selector at /scan-station
// lists these; /scan-station/:stationId loads one. LATHE-ST-01 is bound
// to an Area with Machines (queue → one-shot assign); DEBURR-ST-01 to
// an Area without Machines (direct processing, Machine = NULL). An
// unknown or inactive Station ID shows an explicit error — never a
// silent fallback to another station.
export const MOCK_SCAN_STATIONS: MockScanStation[] = [
  {
    stationId: 'LATHE-ST-01',
    department: 'Machine Shop',
    area: 'lathe',
    active: true,
  },
  {
    stationId: 'CUT-ST-01',
    department: 'Machine Shop',
    area: 'cut',
    active: true,
  },
  {
    stationId: 'DEBURR-ST-01',
    department: 'Machine Shop',
    area: 'deburr',
    active: true,
  },
  {
    stationId: 'EXT-ST-01',
    department: 'Machine Shop',
    area: 'external',
    active: true,
  },
];

export function stationById(stationId: string): MockScanStation | undefined {
  return MOCK_SCAN_STATIONS.find((s) => s.stationId === stationId && s.active);
}

// The Worker session shows `from <badge-scan time> to <shift end>`:
// `since` is the fixed timestamp of the badge scan (mock: anchored
// relative to load), `shiftEnd` the wall-clock end of the shift.
export const MOCK_WORKER = {
  name: 'H. Nguyen',
  since: minutesAgoIso(154),
  shiftEnd: '18:00',
};

// Machine barcode ids of the Lathe Area (mock): PF:MACHINE:<id>.
export const MOCK_MACHINE_BARCODES: Record<
  string,
  { machine: string; area: 'lathe' | 'cut' }
> = {
  L1: { machine: 'Lathe 1', area: 'lathe' },
  L2: { machine: 'Lathe 2', area: 'lathe' },
  L3: { machine: 'Lathe 3', area: 'lathe' },
  L4: { machine: 'Lathe 4', area: 'lathe' },
  S1: { machine: 'Saw 1', area: 'cut' },
};

// Worker barcode ids (mock).
export const MOCK_WORKER_BARCODES: Record<string, string> = {
  '88': 'V. Tran',
  '12': 'H. Nguyen',
};

// Downstream quantity that previously visited the station's Area and
// may deliberately return for repair (mock presentation of the
// movement-history check; the real check derives from PartMovement).
export const MOCK_REPAIR_SOURCES: Record<
  string,
  { areaLabel: string; qty: number; flow: string; note: string }[]
> = {
  '0455-20-0118-03': [
    {
      areaLabel: 'Deburr',
      qty: 4,
      flow: 'QF-0148',
      note: 'turned at Lathe on Jul 24 — may return for repair',
    },
  ],
};
