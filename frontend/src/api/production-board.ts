// Production Board read model API (Phase 11 — GUI_DESIGN §5;
// PROJECT_PROFILE §21).
//
// One read: `GET /api/production-board` returns the Department-wide
// board — every PN in production with its distributed quantity per
// Area / Machine / External activity and the FIXED entry timestamp of
// each position, the stocked and scrapped quantities, the demand
// context (Work Order Number, Job Numbers, requested / allocated
// quantity) and the Hot rank, in the canonical board order, plus the
// Department totals of the footer. The server never sends a derived
// time value: dwell times, the due countdown and `Total Days` derive
// at render from these fixed timestamps and dates plus the shared UI
// clock (§3.12), so the board can never disagree with another view.
//
// Wire responses are the backend's snake_case schemas; this module
// maps them to the camelCase model the view renders.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * One distributed quantity position on the Production Board. The Area,
 * Machine and External activity are explicit presentation data — never
 * one combined display string that would have to be parsed back apart:
 * - `state: 'machine'` — actively assigned; `machine` is the executor.
 * - `state: 'done'` — finished at the Area (`READY_TO_TRANSFER`);
 *   `machine` is optional completion context only, never the executor.
 * - `state: 'queue' | 'processing' | 'stocked'` — no Machine involved.
 */
export interface BoardLocation {
  areaId: number;
  /** Area display label — only the Area name (`External`, never a
   * composite such as `External — Plating`). */
  label: string;
  /** The Area's identity color (Administration), or null. */
  areaColor: string | null;
  /** Machine name — executor for `machine`, context for `done`. */
  machine?: string;
  /** External processing activity (`Plating`, `Vendor`, …) — the
   * external Operation the quantity is out for. Rendered as a light
   * informational chip in the state position, replacing the generic
   * `processing` label; for a DONE row it stays secondary context. */
  activity?: string;
  qty: number;
  state: 'machine' | 'queue' | 'processing' | 'done' | 'stocked';
  /**
   * ISO timestamp when the OLDEST portion of this position entered it,
   * or null when elapsed time does not apply (stocked quantity). The
   * board derives the displayed duration at render from this fixed
   * timestamp plus the shared UI clock — never stored.
   */
  since: string | null;
}

/** One OPEN Work Order Demand named on a board row (Job Numbers column). */
export interface BoardDemand {
  workOrderId: number;
  /** Verbatim external number, or null on an internal Work Order —
   * rendered as `—` (display-only). */
  workOrderNumber: string | null;
  workOrderDemandId: number;
  requestType: string;
  requestedQuantity: number;
  allocatedQuantity: number;
  jobNumbers: string[];
  /** ISO `YYYY-MM-DD`, or null. */
  dueDate: string | null;
  priorityRank: number | null;
}

export interface BoardRow {
  pn: string;
  /** PN master description (name · revision) as a secondary line —
   * absent until Part Numbers management (Phase 13) supplies the
   * metadata; the development long-data preview fills it. */
  name?: string;
  /** Manager-defined Hot rank of the row's defining demand (1 =
   * highest); undefined when not Hot. */
  hotRank?: number;
  locations: BoardLocation[];
  /** Quantity currently in production (active) in the Department. */
  activeQuantity: number;
  /** Quantity stocked in the Department's terminal Areas. */
  stockedQuantity: number;
  /** `activeQuantity + stockedQuantity` — the reconciling total line. */
  total: number;
  /** True when the row's whole quantity is stocked (nothing active). */
  totalStocked: boolean;
  /** Cumulative SCRAPPED quantity for the PN in the Department (0 =
   * none). */
  scrapped: number;
  /** The OPEN demand context in canonical order; the first entry
   * defines the row's Hot rank, due and received dates. Empty when only
   * history (a completed Work Order, found quantity) explains the
   * quantity — the row then carries the server's fallback dates. */
  demands: BoardDemand[];
  /**
   * ISO `YYYY-MM-DD` due date of the row's defining WO Demand, or null
   * when it has no due date — a missing due date is valid data, not an
   * error. The countdown text and urgency class are DERIVED at render
   * (views/dates `dueCountdown` + the shared UI clock), never stored.
   */
  due: string | null;
  /** Received date (ISO) of the defining demand's Work Order — orders
   * undated rows and backs the derived `Total Days` column. */
  received: string;
}

export interface ProductionBoard {
  department: { id: number; name: string };
  rows: BoardRow[];
  /** Footer totals. */
  activePartNumbers: number;
  activeQuantity: number;
  stockedQuantity: number;
  scrappedQuantity: number;
}

// ---------------------------------------------------------------------------
// Wire
// ---------------------------------------------------------------------------

type LocationStateWire =
  'MACHINE' | 'QUEUE' | 'PROCESSING' | 'DONE' | 'STOCKED';

interface BoardLocationWire {
  area: {
    id: number;
    name: string;
    color: string | null;
    is_terminal: boolean;
  };
  machine: { id: number; name: string } | null;
  activity: string | null;
  quantity: number;
  state: LocationStateWire;
  since: string | null;
}

interface BoardDemandWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_demand_id: number;
  request_type: string;
  requested_quantity: number;
  allocated_quantity: number;
  job_numbers: string[];
  due_date: string | null;
  priority_rank: number | null;
}

interface BoardRowWire {
  part_number: string;
  hot_rank: number | null;
  due_date: string | null;
  received_date: string;
  locations: BoardLocationWire[];
  active_quantity: number;
  stocked_quantity: number;
  scrapped_quantity: number;
  total_quantity: number;
  demands: BoardDemandWire[];
}

interface ProductionBoardWire {
  department: { id: number; name: string };
  rows: BoardRowWire[];
  active_part_numbers: number;
  active_quantity: number;
  stocked_quantity: number;
  scrapped_quantity: number;
}

const LOCATION_STATE: Record<LocationStateWire, BoardLocation['state']> = {
  MACHINE: 'machine',
  QUEUE: 'queue',
  PROCESSING: 'processing',
  DONE: 'done',
  STOCKED: 'stocked',
};

function toLocation(wire: BoardLocationWire): BoardLocation {
  return {
    areaId: wire.area.id,
    label: wire.area.name,
    areaColor: wire.area.color,
    ...(wire.machine ? { machine: wire.machine.name } : {}),
    ...(wire.activity ? { activity: wire.activity } : {}),
    qty: wire.quantity,
    state: LOCATION_STATE[wire.state],
    since: wire.since,
  };
}

function toDemand(wire: BoardDemandWire): BoardDemand {
  return {
    workOrderId: wire.work_order_id,
    workOrderNumber: wire.work_order_number,
    workOrderDemandId: wire.work_order_demand_id,
    requestType: wire.request_type,
    requestedQuantity: wire.requested_quantity,
    allocatedQuantity: wire.allocated_quantity,
    jobNumbers: wire.job_numbers,
    dueDate: wire.due_date,
    priorityRank: wire.priority_rank,
  };
}

function toRow(wire: BoardRowWire): BoardRow {
  return {
    pn: wire.part_number,
    ...(wire.hot_rank !== null ? { hotRank: wire.hot_rank } : {}),
    locations: wire.locations.map(toLocation),
    activeQuantity: wire.active_quantity,
    stockedQuantity: wire.stocked_quantity,
    total: wire.total_quantity,
    totalStocked: wire.active_quantity === 0,
    scrapped: wire.scrapped_quantity,
    demands: wire.demands.map(toDemand),
    due: wire.due_date,
    received: wire.received_date,
  };
}

function toBoard(wire: ProductionBoardWire): ProductionBoard {
  return {
    department: wire.department,
    rows: wire.rows.map(toRow),
    activePartNumbers: wire.active_part_numbers,
    activeQuantity: wire.active_quantity,
    stockedQuantity: wire.stocked_quantity,
    scrappedQuantity: wire.scrapped_quantity,
  };
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

/**
 * Load the board. `departmentId` selects the Department; omitted, the
 * server resolves the single active Department and refuses an
 * ambiguous configuration (several active Departments) with an
 * explicit message naming them.
 */
export async function loadProductionBoard(
  departmentId: number | null,
): Promise<ProductionBoard> {
  const query =
    departmentId !== null
      ? `?department_id=${encodeURIComponent(departmentId)}`
      : '';
  const wire = await apiRequest<ProductionBoardWire>(
    `/api/production-board${query}`,
  );
  return toBoard(wire);
}
