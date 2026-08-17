import type { AreaKey, MockScanStation } from '../views/view-models';
import { activeMachines } from './machines';

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

// Worker registry (mock). Workers are Scan-Station-scoped audit
// identity, never application accounts (PROJECT_PROFILE §7/§8.13):
// stable internal id, name, badge barcode, avatar, active status — no
// employee number. The badge barcode is the barcode already printed on
// the company's existing employee badge (non-PF; §10) and is
// exact-matched; the mock avatar is initials (a real avatar image
// arrives with the backend).
export interface MockWorker {
  id: string;
  name: string;
  badgeBarcode: string;
  avatar: string;
  active: boolean;
}

export const MOCK_WORKERS: MockWorker[] = [
  {
    id: 'wkr-01',
    name: 'H. Nguyen',
    badgeBarcode: '100482',
    avatar: 'HN',
    active: true,
  },
  {
    id: 'wkr-02',
    name: 'V. Tran',
    badgeBarcode: '100517',
    avatar: 'VT',
    active: true,
  },
  // Inactive Worker: the badge matches nothing — scans are rejected.
  {
    id: 'wkr-03',
    name: 'T. Pham',
    badgeBarcode: '100290',
    avatar: 'TP',
    active: false,
  },
];

/**
 * Exact-match a scanned non-PF value against ACTIVE Worker badge
 * barcodes (PROJECT_PROFILE §10). Unknown or inactive badges resolve
 * to nothing — never guessed.
 */
export function workerByBadge(value: string): MockWorker | undefined {
  return MOCK_WORKERS.find((w) => w.active && w.badgeBarcode === value);
}

export function workerById(id: string): MockWorker | undefined {
  return MOCK_WORKERS.find((w) => w.id === id);
}

// Configured Worker of each Fixed-Worker Area (mock configuration).
export const MOCK_FIXED_WORKERS: Partial<Record<AreaKey, string>> = {
  cut: 'wkr-02',
  manual: 'wkr-02',
  deburr: 'wkr-01',
};

export function fixedWorkerFor(area: AreaKey): MockWorker | null {
  const id = MOCK_FIXED_WORKERS[area];
  return (id && workerById(id)) || null;
}

// Scanned Worker Sessions expire through a sliding inactivity timeout
// (PROJECT_PROFILE §19): one Administration default with optional
// per-Area overrides — never a shift schedule, never derived from the
// number of Workers in an Area.
export const MOCK_WORKER_SESSION_POLICY = {
  defaultTimeoutMinutes: 15,
  areaOverrides: { lathe: 20 } as Partial<Record<AreaKey, number>>,
};

export function workerSessionTimeoutMinutes(area: AreaKey): number {
  return (
    MOCK_WORKER_SESSION_POLICY.areaOverrides[area] ??
    MOCK_WORKER_SESSION_POLICY.defaultTimeoutMinutes
  );
}

/**
 * Badge confirmation of sensitive actions (post-v18, PROJECT_PROFILE
 * §19): in a Scanned-session Area each of the three sensitive one-shot
 * actions can require a Worker badge scan as the FINAL step after the
 * confirmation summary — DONE (Complete Area processing), QUEUE
 * (Return unfinished quantity to queue) and UNDO each carry their own
 * option, default ON. Mock configuration: Administration → Worker
 * sessions edits the values session-only (never persisted); the Scan
 * Station reads them when the final step opens. Fixed-Worker Areas ask
 * a final toned confirmation question instead — no badge exists there.
 */
export type BadgeConfirmAction = 'done' | 'queue' | 'undo';

export const MOCK_BADGE_CONFIRM_POLICY: Record<BadgeConfirmAction, boolean> = {
  done: true,
  queue: true,
  undo: true,
};

export function requireBadgeConfirm(action: BadgeConfirmAction): boolean {
  return MOCK_BADGE_CONFIRM_POLICY[action];
}

export function setBadgeConfirmRequirement(
  action: BadgeConfirmAction,
  required: boolean,
): void {
  MOCK_BADGE_CONFIRM_POLICY[action] = required;
}

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
