// Shared view-model types for the approved GUI views.
//
// These types describe the data shapes the views render. They are
// production-safe (types only, no sample data) and are consumed by both
// the development-only mock datasets in src/mocks/ and by shared
// presentation components. Real read models supply the same shapes in
// later phases.

export type AreaKey =
  | 'material'
  | 'cut'
  | 'lathe'
  | 'mill'
  | 'manual'
  | 'deburr'
  | 'external'
  | 'stockroom';

export type DueClass = 'ok' | 'soon' | 'late';

export type RequestType = 'NEW' | 'REWORK' | 'MODIFY';

/** Canonical PartMovement event types surfaced by the mock views. */
export type MovementType =
  'RECEIVED' | 'TRANSFERRED' | 'ASSIGNED_TO_MACHINE' | 'SPLIT' | 'REVERSED';

export interface MockArea {
  key: AreaKey;
  name: string;
  /** CSS custom property carrying the stable Area identity color. */
  colorVar: string;
  description: string;
  operations: string[];
  terminal?: boolean;
}

export type MachineStatus = 'running' | 'idle' | 'maintenance';

/** One Machine belonging to an Area (Area Board detail monitoring). */
export interface MockAreaMachine {
  name: string;
  status: MachineStatus;
}

export interface MockLocationRow {
  area: AreaKey;
  label: string;
  qty: number;
  tag?: 'machine' | 'queue';
  time: string;
  timeLong?: boolean;
}

export interface MockBoardRow {
  pn: string;
  name: string;
  hotRank?: number;
  locations: MockLocationRow[];
  total: number;
  totalStocked?: boolean;
  jobs: { job: string; meta: string }[];
  /**
   * ISO `YYYY-MM-DD` due date of the WO Demand, or null when the demand
   * has no due date — a missing due date is valid data, not an error.
   */
  due: string | null;
  dueNote: string;
  dueClass: DueClass | 'none';
  totalDays: string;
  /** Parent Work Order received date (ISO) — orders undated demands. */
  received: string;
}

export interface MockAreaCard {
  area: AreaKey;
  pn: string;
  /** Work Order context label, e.g. `WO 007003 · Receiving`. */
  workOrder: string;
  job: string;
  qty: number;
  machines: [string, number][];
  due: string;
  dueClass: DueClass | 'none';
  timeInArea: string;
  hotRank?: number;
  /** Days until due; null when the WO Demand has no due date. */
  dueDays: number | null;
  /** Sortable duration for `Sort: Time in Area` (mock-derived). */
  timeInAreaMinutes: number;
  /** Parent Work Order received date (ISO) — orders undated demands. */
  received: string;
}

export interface MockTrackingRow {
  pn: string;
  name: string;
  hotRank?: number;
  demand: { workOrder: string; qty: number; type: RequestType }[];
  distribution: { area: AreaKey; label: string; qty: number }[];
  activeQty: number;
  stockedQty: number;
  /** Display text; `—` means the relevant WO Demand has no due date. */
  nextDue: string;
  status: 'Active' | 'Stocked' | 'Completed';
}

export interface MockWorkOrderLine {
  pn: string;
  barcode: string;
  type: RequestType;
  qty: number;
  /** ISO `YYYY-MM-DD`, or null when the demand line has no due date. */
  due: string | null;
  job: string;
  notes?: string;
  status: string;
  statusClass: 'saved' | 'released' | 'invalid';
  releasable?: boolean;
}

export interface MockWorkOrder {
  /**
   * Opaque external Work Order Number (no fixed format), or a generated
   * temporary internal number (`TMP-YYYYMMDD-HHMMSS`) when the user
   * saved demand without knowing one. Never null — the internal Work
   * Order identity always exists.
   */
  workOrderNumber: string;
  /** ISO `YYYY-MM-DD`; required — defaults to today at manual entry. */
  received: string;
  /**
   * ISO `YYYY-MM-DD` entry default for demand-line due dates, or null —
   * a Work Order may be saved without a due date.
   */
  due: string | null;
  dueClass: DueClass | '';
  status: 'Open' | 'Released' | 'Complete';
  internal?: boolean;
  preview: string;
  lines: MockWorkOrderLine[];
}

export interface MockHotEntry {
  pn: string;
  /** Work Order Demand label, e.g. `WO 007001 · Job 18112`. */
  workOrder: string;
  type: RequestType;
  figures: string[];
  /** ISO `YYYY-MM-DD`, or null when the demand has no due date. */
  due: string | null;
  dueNote: string;
  dueClass: DueClass | 'none';
  /** PN barcode accepted by the add-to-Hot search field (mock only). */
  barcode?: string;
}

export interface MockScanRecord {
  pn: string;
  /** Movement type — rendered separately, never embedded in the text. */
  movementType: MovementType;
  description: string;
  time: string;
  reversed?: boolean;
}

export interface MockInventoryRow {
  pn: string;
  where: string;
  qty: number;
}

export interface MockMachineTile {
  name: string;
  note: string;
  maintenance?: boolean;
}
