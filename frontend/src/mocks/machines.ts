import type { MockMachine } from '../views/view-models';

// Machine registry (development mock): the single source for Machine
// identity, lifecycle, maintenance overrides, and asset metadata.
// Operational running/idle states are DERIVED from the assigned
// quantity in mocks/area-board.ts (see views/machine-state.ts) — they
// are never stored here. `stateChangedAt` timestamps are anchored
// relative to module load so the derived state ages stay realistic.
//
// The two `Lathe 1` records demonstrate physical replacement: the old
// Machine record is retired untouched (its identity, barcode and asset
// metadata keep pointing at the Machine that really did the work) and
// the replacement is a NEW record with its own identity and barcode
// that reuses the familiar floor-position display name.

const minutesAgo = (minutes: number): string =>
  new Date(Date.now() - minutes * 60_000).toISOString();

/**
 * Mock actor for management lifecycle changes (retire / reactivate).
 * Phase 2 has no signed-in management user yet — the audit shape is
 * exercised with one fixed development actor.
 */
export const MOCK_MACHINE_ACTOR = 'M. Chen (Production Manager)';

export const MOCK_MACHINES: MockMachine[] = [
  {
    id: 'MC-201',
    area: 'cut',
    name: 'Saw 1',
    barcode: 'S1',
    stateChangedAt: minutesAgo(220),
    manufacturer: 'Amada',
    model: 'HFA-250W',
    assetTag: 'CD-0201',
    serialNumber: 'HF25-33017',
    installedOn: '2018-03-12',
  },
  {
    // Replacement Machine for the retired MC-104: new identity, new
    // barcode, reused display name for the familiar floor position.
    id: 'MC-512',
    area: 'lathe',
    name: 'Lathe 1',
    barcode: 'L1',
    stateChangedAt: minutesAgo(18),
    manufacturer: 'Mazak',
    model: 'QT-250',
    assetTag: 'CD-0512',
    serialNumber: 'Q25-90412',
    installedOn: '2026-02-16',
  },
  {
    id: 'MC-105',
    area: 'lathe',
    name: 'Lathe 2',
    barcode: 'L2',
    stateChangedAt: minutesAgo(65),
    manufacturer: 'Mazak',
    model: 'QT-15',
    assetTag: 'CD-0105',
    serialNumber: 'Q15-88472',
    installedOn: '2014-09-20',
  },
  {
    id: 'MC-106',
    area: 'lathe',
    name: 'Lathe 3',
    barcode: 'L3',
    stateChangedAt: minutesAgo(125),
    manufacturer: 'Haas',
    model: 'ST-20',
    assetTag: 'CD-0106',
    serialNumber: 'ST20-51230',
    installedOn: '2019-05-02',
  },
  {
    id: 'MC-107',
    area: 'lathe',
    name: 'Lathe 4',
    barcode: 'L4',
    // Explicit maintenance override with an expected return date —
    // running/idle stay derived, maintenance is always a decision.
    maintenance: {
      since: minutesAgo(3060),
      expectedReturn: '2026-08-06',
      note: 'Spindle bearing replacement',
    },
    stateChangedAt: minutesAgo(3060),
    manufacturer: 'Haas',
    model: 'ST-20',
    assetTag: 'CD-0107',
    serialNumber: 'ST20-51301',
    installedOn: '2019-05-02',
  },
  {
    id: 'MC-301',
    area: 'mill',
    name: 'Mill 1',
    barcode: 'M1',
    stateChangedAt: minutesAgo(45),
    manufacturer: 'Haas',
    model: 'VF-2',
    assetTag: 'CD-0301',
    serialNumber: 'VF2-77841',
    installedOn: '2020-11-05',
  },
  {
    id: 'MC-302',
    area: 'mill',
    name: 'Mill 2',
    barcode: 'M2',
    stateChangedAt: minutesAgo(80),
    manufacturer: 'Haas',
    model: 'VF-4',
    assetTag: 'CD-0302',
    serialNumber: 'VF4-80233',
    installedOn: '2021-04-19',
  },
  {
    id: 'MC-303',
    area: 'mill',
    name: 'Mill 3 — Horizontal Boring',
    barcode: 'M3',
    stateChangedAt: minutesAgo(370),
    manufacturer: 'Toshiba',
    model: 'BTD-110',
    assetTag: 'CD-0303',
    serialNumber: 'BT11-40518',
    installedOn: '2016-08-30',
  },
  {
    // Retired predecessor of MC-512 — same display name, different
    // physical asset. Kept for historical display and reporting; it
    // accepts no new work and is never renamed to hide the swap.
    id: 'MC-104',
    area: 'lathe',
    name: 'Lathe 1',
    barcode: 'M-0104',
    retiredOn: '2026-02-14',
    stateChangedAt: '2026-02-14T16:00:00.000Z',
    lifecycle: [
      {
        event: 'RETIRED',
        at: '2026-02-14T16:00:00.000Z',
        by: MOCK_MACHINE_ACTOR,
        reason: 'Replaced by asset CD-0512',
      },
    ],
    manufacturer: 'Mazak',
    model: 'QT-10',
    assetTag: 'CD-0104',
    serialNumber: 'Q10-61208',
    installedOn: '2012-06-01',
    notes:
      'Replaced by asset CD-0512 — display name reused for the floor position.',
  },
  {
    // Retired Machine WITHOUT an asset tag: the typed retirement /
    // reactivation confirmation falls back to the Machine barcode for
    // records like this one (barcode is always present).
    id: 'MC-202',
    area: 'cut',
    name: 'Saw 2',
    barcode: 'S2',
    retiredOn: '2025-11-03',
    stateChangedAt: '2025-11-03T09:30:00.000Z',
    lifecycle: [
      {
        event: 'RETIRED',
        at: '2025-11-03T09:30:00.000Z',
        by: MOCK_MACHINE_ACTOR,
        reason: 'Gearbox failure — not economical to repair',
      },
    ],
    manufacturer: 'Behringer',
    model: 'HBP-263A',
    installedOn: '2011-04-08',
    notes: 'Kept in storage — may return to service after overhaul.',
  },
];

/** Active (non-retired) Machines — the only ones that may take work. */
export function activeMachines(): MockMachine[] {
  return MOCK_MACHINES.filter((m) => m.retiredOn === undefined);
}

/** Retired Machines — historical display and reporting only. */
export function retiredMachines(): MockMachine[] {
  return MOCK_MACHINES.filter((m) => m.retiredOn !== undefined);
}
