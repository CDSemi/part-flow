import type { AreaKey, MockScanStation } from '../views/view-models';
import { activeMachines } from './machines';
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

// Machine barcode resolution (mock): PF:MACHINE:<asset-tag>, derived
// from the shared Machine registry so the scanned value is always the
// Machine's Asset Tag — there is no independent barcode registry.
// Retired Machines are excluded: their barcodes accept no new scans.
export const MOCK_MACHINE_BARCODES: Record<
  string,
  { machine: string; area: AreaKey }
> = Object.fromEntries(
  activeMachines().map((m) => [m.barcode, { machine: m.name, area: m.area }]),
);

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
