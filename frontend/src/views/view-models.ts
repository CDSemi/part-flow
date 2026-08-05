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

/**
 * Canonical PartMovement event types surfaced by the mock views.
 * `AREA_COMPLETED` records that a selected quantity finished processing
 * at its current Area (user-facing DONE): the current Machine clears,
 * the Area stays the physical location, and the derived holding state
 * is `READY_TO_TRANSFER` — waiting on the finished rack for transfer.
 * It is never Work Order completion, Stockroom completion (`STOCKED`),
 * allocation, or QC approval.
 */
export type MovementType =
  | 'RECEIVED'
  | 'TRANSFERRED'
  | 'ASSIGNED_TO_MACHINE'
  | 'RELEASED_FROM_MACHINE'
  | 'AREA_COMPLETED'
  | 'SPLIT'
  | 'STOCKED'
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

/**
 * Effective operational state of an active Machine. Maintenance is an
 * explicit override; otherwise a Machine with assigned active quantity
 * is `running` and an unassigned active Machine is `idle` — running
 * and idle are always derived, never chosen by a user.
 */
export type MachineStatus = 'running' | 'idle' | 'maintenance';

/** One Machine belonging to an Area (Area/Machine monitoring cards). */
export interface MockAreaMachine {
  name: string;
  status: MachineStatus;
  /**
   * ISO timestamp of the moment the Machine entered its current
   * operational state. Every surface derives the visible elapsed time
   * (`running · 1h 24m`) from this shared timestamp — a formatted
   * duration is never stored, so no two views can disagree.
   */
  stateChangedAt: string;
  /** Maintenance context (status `maintenance` only): optional note. */
  maintenanceNote?: string;
  /** Expected return date (ISO `YYYY-MM-DD`), when one was given. */
  expectedReturn?: string;
}

/**
 * One append-only Machine lifecycle event (v15). The lifecycle audit
 * distinguishes RETIRED and REACTIVATED (return-to-service of the SAME
 * physical machine — never a replacement, which is always a new
 * record); activation at creation stays implicit. Events are never
 * edited or removed: the record of when a Machine was out of service
 * survives a reactivation.
 */
export interface MachineLifecycleEvent {
  event: 'RETIRED' | 'REACTIVATED';
  /** ISO timestamp of the lifecycle change. */
  at: string;
  /** Who performed the change (mock actor in Phase 2). */
  by: string;
  reason?: string;
  /** Set on REACTIVATED when the physical machine moved while retired
   * — applies forward only; historical Movements keep their Areas. */
  fromArea?: AreaKey;
  toArea?: AreaKey;
}

/**
 * One physical Machine record (Management → Machines). A Machine is a
 * specific physical production resource with a stable internal
 * identity and its own barcode. A replacement Machine is a NEW record
 * (new identity, new barcode) that may reuse the operator-facing
 * display name of a familiar floor position (`Lathe 1`) — the retired
 * record is never renamed or mutated, so history keeps pointing at the
 * Machine that really did the work. Return to service of the SAME
 * physical machine is the one exception (v15): reactivation clears
 * `retiredOn` on the same record and appends a lifecycle event —
 * identity, barcode, asset metadata and history stay untouched.
 */
export interface MockMachine {
  /** Stable internal identity — never reused by a replacement. */
  id: string;
  area: AreaKey;
  /** Operator-facing display name; reusable across replacements. */
  name: string;
  /** Machine barcode value (`PF:MACHINE:<value>`); unique, stable. */
  barcode: string;
  /**
   * Explicit maintenance override. Entering maintenance never moves,
   * releases, completes, or transfers assigned quantity.
   */
  maintenance?: {
    /** ISO timestamp when maintenance started. */
    since: string;
    /** Optional expected return date (ISO `YYYY-MM-DD`). */
    expectedReturn?: string;
    /** Optional reason shown on monitoring surfaces. */
    note?: string;
  };
  /**
   * Set when the Machine is retired: it stays visible in historical
   * context and reporting but never receives new assignments or scans.
   * Retirement requires the assigned quantity to be handled through
   * the normal production workflow first.
   */
  retiredOn?: string;
  /** ISO timestamp of the last operational state change. */
  stateChangedAt: string;
  /**
   * Append-only lifecycle audit (v15): RETIRED / REACTIVATED events in
   * chronological order. Absent or empty = never retired.
   */
  lifecycle?: MachineLifecycleEvent[];
  /* Optional asset metadata — identification of the physical asset
     (the Asset Tag stays unique even when display names are reused).
     Production tracking never depends on these fields. */
  manufacturer?: string;
  model?: string;
  assetTag?: string;
  serialNumber?: string;
  installedOn?: string;
  notes?: string;
}

/** One expected step of a Planned Route (Route Template). */
export interface MockRouteStep {
  area: AreaKey;
  operation: string;
  /** Advisory expected duration, e.g. `4h` — never blocks production. */
  expectedDuration?: string;
  instructions?: string;
  /**
   * Preferred (not mandatory) Machine — the stable Machine id (v15,
   * matching `preferred_machine_id`), never the reusable display name:
   * `Lathe 1` may mean two different physical machines across a
   * replacement, so a name cannot identify the preference. A retired
   * or missing Machine renders as an explicit unavailable value —
   * never silently cleared.
   */
  preferredMachineId?: string;
}

/**
 * One reusable Planned Route definition (internal name: RouteTemplate).
 * Editing a template affects FUTURE assignments only — a released
 * Quantity Flow keeps its independent Assigned Route snapshot. A
 * template that has ever been used is archived instead of deleted;
 * archived templates stay visible in historical context but never
 * appear as normal choices for new assignments.
 */
export interface MockRouteTemplate {
  /** Stable internal identity. */
  id: string;
  name: string;
  description?: string;
  steps: MockRouteStep[];
  /** ISO date the template was archived; absent = active. */
  archivedOn?: string;
  /**
   * Where the template has been used: Quantity Flows released with an
   * Assigned Route snapshot copied from it. Empty = never used (such a
   * template may be deleted outright).
   */
  usedBy: { flow: string; pn: string; releasedOn: string }[];
  createdOn: string;
  updatedOn: string;
}

/**
 * One distributed quantity position on the Production Board. The Area,
 * Machine and External activity are explicit presentation data — never
 * one combined display string that would have to be parsed back apart:
 * - `state: 'machine'` — actively assigned; `machine` is the executor.
 * - `state: 'done'` — finished at the Area (`READY_TO_TRANSFER`);
 *   `machine` is optional completion context only, never the executor.
 * - `state: 'queue' | 'processing' | 'stocked'` — no Machine involved.
 */
export interface MockLocationRow {
  area: AreaKey;
  /**
   * Area display label — only the Area name (`External`, never a
   * composite such as `External — Plating`).
   */
  label: string;
  /** Machine name — executor for `machine`, context for `done`. */
  machine?: string;
  /**
   * External processing activity (`plating`, `vendor`, `painting`, …).
   * Rendered as a light informational chip in the state position,
   * replacing the generic `processing` label; for a DONE row it stays
   * secondary context only.
   */
  activity?: string;
  qty: number;
  state: 'machine' | 'queue' | 'processing' | 'done' | 'stocked';
  /**
   * ISO timestamp when this quantity portion entered its current
   * position, or null when elapsed time does not apply (Stockroom).
   * The board derives the displayed duration at render from this fixed
   * timestamp plus the shared UI clock — a formatted duration is never
   * stored, so no two views can disagree.
   */
  since: string | null;
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
   * The countdown text and urgency class are DERIVED at render
   * (views/dates `dueCountdown` + the shared UI clock), never stored.
   */
  due: string | null;
  /** Parent Work Order received date (ISO) — orders undated demands and
   * backs the derived `Total Days` column. */
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
  /**
   * ISO `YYYY-MM-DD` due date of the WO Demand, or null when it has no
   * due date. The countdown text and urgency class are DERIVED at
   * render (views/dates `dueCountdown` + the shared UI clock).
   */
  due: string | null;
  /**
   * Verbatim status text replacing the derived due countdown where a
   * countdown does not apply (e.g. the Stockroom `allocated 50/50`).
   */
  dueText?: string;
  /**
   * ISO timestamp when this PN presence entered the Area, or null when
   * elapsed time does not apply (Stockroom). `Time in Area` and its
   * sort order are DERIVED from it at render — never stored.
   */
  enteredAreaAt: string | null;
  hotRank?: number;
  /** Parent Work Order received date (ISO) — orders undated demands. */
  received: string;
  /** Damaged quantity scrapped from this PN in this Area (mock). */
  scrapped?: number;
  /**
   * Portions of `qty` that finished processing at this Area and wait on
   * the finished rack for transfer (`READY_TO_TRANSFER`). `completedBy`
   * is completion context only — the current Machine is cleared, and
   * the quantity remains located in this Area until transferred.
   * Quantity conservation: Machine/queue portions + finished portions
   * (+ the direct-processing remainder in a no-Machine Area) = `qty`.
   */
  finished?: { qty: number; completedBy?: string }[];
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
  /**
   * External Work Order Number, or null for an internal Work Order
   * without an external number (displays `—`). Explicit field — the
   * confirmation dialogs never parse it out of the display label.
   */
  workOrderNumber: string | null;
  /** External Job Number, or null when the demand has none. */
  jobNumber: string | null;
  type: RequestType;
  figures: string[];
  /**
   * ISO `YYYY-MM-DD`, or null when the demand has no due date. The
   * countdown text and urgency class are DERIVED at render
   * (views/dates `dueCountdown` + the shared UI clock), never stored.
   */
  due: string | null;
  /** PN barcode accepted by the add-to-Hot search field (mock only). */
  barcode?: string;
}

/**
 * One completed PN operation eligible for Undo. The Scan Station keeps
 * a session-local stack of these; Undo always shows this summary before
 * a compensating reversal is applied (never a deletion). One operation
 * is one atomic application command: when a single user action appends
 * several related Movement events (e.g. `AREA_COMPLETED` followed by
 * `TRANSFERRED` for a transfer that implicitly completes source
 * processing), `movements` lists them in order and Undo reverses the
 * whole command — never one arbitrary row.
 */
export interface MockCompletedAction {
  pn: string;
  /** Movement events of the command, in append order. */
  movements: MovementType[];
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
