import type { AssetTagFormat } from '../views/asset-tags';

/**
 * Development sample of the configured Machine Asset Tag format
 * (Administration → Barcode configuration): prefix + zero-padded
 * numeric sequence. The mock Machine registry's tags (`CD-0512`, …)
 * follow this format; Management → Machines assigns the next tag from
 * it when a new Machine is created.
 */
export const MOCK_ASSET_TAG_FORMAT: AssetTagFormat = {
  prefix: 'CD-',
  digits: 4,
};

export interface MockAdminAreaRow {
  areaKey: string;
  name: string;
  operations: string;
  machineMode: string;
  machines: string;
  workerMode: string;
  terminal?: boolean;
  active: boolean;
}

/** The three per-Area Worker ID modes (PROJECT_PROFILE §8.13/§19). */
export type WorkerIdMode = 'disabled' | 'fixed' | 'scanned';

/**
 * Typed Worker ID mode of one Area, derived from the same configuration
 * rows the Administration Areas table displays — one source, no
 * duplicate mode registry.
 */
export function workerIdModeFor(areaKey: string): WorkerIdMode {
  const row = MOCK_ADMIN_AREAS.find((r) => r.areaKey === areaKey);
  switch (row?.workerMode) {
    case 'Fixed Worker':
      return 'fixed';
    case 'Scanned session':
      return 'scanned';
    default:
      return 'disabled';
  }
}

export const MOCK_ADMIN_AREAS: MockAdminAreaRow[] = [
  {
    areaKey: 'material',
    name: 'Material',
    operations: 'Receiving',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Disabled',
    active: true,
  },
  {
    areaKey: 'cut',
    name: 'Cut',
    operations: 'Cutting',
    // One Machine behaves exactly like several: queue → one-shot
    // assignment. Behavior never differs by Machine count.
    machineMode: 'Queue → assign (one-shot)',
    machines: 'Saw 1',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'lathe',
    name: 'Lathe',
    operations: 'Turning',
    machineMode: 'Queue → assign (one-shot)',
    machines: 'Lathe 1–4',
    workerMode: 'Scanned session',
    active: true,
  },
  {
    areaKey: 'mill',
    name: 'Mill',
    operations: 'Milling',
    machineMode: 'Queue → assign (one-shot)',
    machines: 'Mill 1–2',
    workerMode: 'Scanned session',
    active: true,
  },
  {
    areaKey: 'manual',
    name: 'Manual',
    operations: 'Manual work',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'deburr',
    name: 'Deburr',
    operations: 'Deburring',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'external',
    name: 'External',
    operations: 'Plating · Painting · Testing',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Disabled',
    active: true,
  },
  {
    areaKey: 'stockroom',
    name: 'Stockroom',
    operations: 'Receiving',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Scanned session',
    terminal: true,
    active: true,
  },
];

// The Administration section registry moved to
// src/views/administration/sections.ts — it is static navigation
// configuration the real Administration view needs in production,
// not sample data.
