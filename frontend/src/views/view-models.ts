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

export interface MockArea {
  key: AreaKey;
  name: string;
  /** CSS custom property carrying the stable Area identity color. */
  colorVar: string;
  description: string;
  operations: string[];
  terminal?: boolean;
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
  blink?: boolean;
  locations: MockLocationRow[];
  total: number;
  totalStocked?: boolean;
  jobs: { job: string; meta: string }[];
  due: string;
  dueNote: string;
  dueClass: DueClass | 'none';
  totalDays: string;
}

export interface MockAreaCard {
  area: AreaKey;
  pn: string;
  po: string;
  job: string;
  qty: number;
  machines: [string, number][];
  due: string;
  dueClass: DueClass;
  timeInArea: string;
  hotRank?: number;
  dueDays: number;
}

export interface MockTrackingRow {
  pn: string;
  name: string;
  hotRank?: number;
  demand: { po: string; qty: number; type: RequestType }[];
  distribution: { area: AreaKey; label: string; qty: number }[];
  activeQty: number;
  stockedQty: number;
  nextDue: string;
  status: 'Active' | 'Stocked' | 'Completed';
}

export interface MockPoLine {
  pn: string;
  barcode: string;
  type: RequestType;
  qty: number;
  /** ISO `YYYY-MM-DD`; presentation formats it for display. */
  due: string;
  job: string;
  notes?: string;
  status: string;
  statusClass: 'saved' | 'released' | 'invalid';
  releasable?: boolean;
}

export interface MockPo {
  po: string;
  /** ISO `YYYY-MM-DD`; presentation formats it for display. */
  received: string;
  /** ISO `YYYY-MM-DD`; the entry default for demand-line due dates. */
  due: string;
  dueClass: DueClass | '';
  status: 'Open' | 'Released' | 'Complete';
  internal?: boolean;
  preview: string;
  lines: MockPoLine[];
}

export interface MockHotEntry {
  pn: string;
  po: string;
  type: RequestType;
  figures: string[];
  due: string;
  dueNote: string;
  dueClass: DueClass;
  /** PN barcode accepted by the add-to-Hot search field (mock only). */
  barcode?: string;
}

export interface MockScanRecord {
  pn: string;
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
