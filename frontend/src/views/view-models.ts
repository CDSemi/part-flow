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

/**
 * Why business demand exists. `NEW` is external ERP demand; `MODIFY`
 * is internal demand for physical quantity introduced for modification
 * work. Repair is NOT a Request Type — it is a movement intent
 * (`movement_reason = REPAIR`) on a transfer of existing quantity.
 */
export type RequestType = 'NEW' | 'MODIFY';

/**
 * How a Quantity Flow relates to routing. `FLOATING` (the default) has
 * no predefined sequence — the actual route trace is derived from
 * immutable Movement history. `PLANNED` carries an AssignedRoute
 * snapshot as guidance; actual Movement stays authoritative.
 */
export type RouteMode = 'FLOATING' | 'PLANNED';

/** Canonical PartMovement event types surfaced by the mock views. */
export type MovementType =
  | 'RECEIVED'
  | 'TRANSFERRED'
  | 'ASSIGNED_TO_MACHINE'
  | 'RELEASED_FROM_MACHINE'
  | 'SPLIT'
  | 'QUANTITY_ADJUSTED'
  | 'SCRAPPED'
  | 'REVERSED';

/**
 * Typed movement intent recorded with a transfer. `REPAIR` marks an
 * explicit return of quantity to a previously visited Area to correct
 * earlier work — never inferred, always chosen by the user.
 */
export type MovementReason = 'REPAIR';

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

/** One Machine belonging to an Area (Area/Machine monitoring cards). */
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
  /** Cumulative SCRAPPED quantity for the PN (0/absent = none). */
  scrapped?: number;
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

/**
 * One PN presence in one Area — the shared row model for the Area/
 * Machine monitoring surfaces (Area Board detail and Scan Station).
 */
export interface MockAreaCard {
  area: AreaKey;
  pn: string;
  /**
   * Work Order context label, e.g. `WO 007003 · Receiving`. A Work
   * Order without an external number displays `—` (e.g. `WO — · MODIFY`);
   * the placeholder is display-only and never persisted.
   */
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
  /** Damaged quantity scrapped from this PN in this Area (mock). */
  scrapped?: number;
}

export interface MockTrackingRow {
  pn: string;
  name: string;
  hotRank?: number;
  /** `workOrder: '—'` = internal demand without an external WO Number. */
  demand: { workOrder: string; qty: number; type: RequestType }[];
  distribution: { area: AreaKey; label: string; qty: number }[];
  activeQty: number;
  stockedQty: number;
  /** Cumulative SCRAPPED quantity (0 = none). */
  scrappedQty: number;
  /** Display text; `—` means the relevant WO Demand has no due date. */
  nextDue: string;
  status: 'Active' | 'Stocked' | 'Completed';
  /** Marks an archived/soft-deleted PN kept for history display. */
  archived?: boolean;
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
  /** Stable internal identity — never shown as the WO identifier. */
  id: string;
  /**
   * Opaque external Work Order Number (no fixed format), or null when
   * no external number is known. A null number is valid data — it
   * displays as `—` and may be filled in later through an audited
   * edit. The `—` placeholder is never persisted. Uniqueness applies
   * to non-null numbers only.
   */
  workOrderNumber: string | null;
  /** ISO `YYYY-MM-DD`; required — defaults to today at manual entry. */
  received: string;
  /**
   * ISO `YYYY-MM-DD` entry default for demand-line due dates, or null —
   * a Work Order may be saved without a due date.
   */
  due: string | null;
  dueClass: DueClass | '';
  status: 'Open' | 'Released' | 'Complete';
  /** True for internal Work Orders without an external number. */
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

/**
 * One completed PN operation eligible for Undo. The Scan Station keeps
 * a session-local stack of these; Undo always shows this summary before
 * a compensating reversal is applied (never a deletion).
 */
export interface MockCompletedAction {
  pn: string;
  movementType: MovementType;
  /** One-line action summary, e.g. `Material → Lathe queue · qty 4`. */
  description: string;
  qty: number;
  source: string;
  destination: string;
  machine?: string;
  worker: string;
  time: string;
  /** What the compensating reversal restores. */
  reversalEffect: string;
}

/** One selectable Scan Station (development mock registry). */
export interface MockScanStation {
  stationId: string;
  department: string;
  area: AreaKey;
  active: boolean;
}
