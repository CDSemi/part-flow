// Hot list API (Phase 12 — Priority Management; GUI_DESIGN §8;
// PROJECT_PROFILE §21).
//
// Three calls against the one Department's Hot Work Order Demand list:
// - `GET /api/hot-list` — the ranked entries, inactive ones included;
// - `GET /api/hot-list/candidates` — the eligible (unranked, active,
//   open Work Order) demand, by free-text search or by PN barcode;
// - `POST /api/hot-list/changes` — the ONE command: exactly one
//   single-entry delta (add at the bottom, remove, move) against the
//   full order the user confirmed against (`expectedOrder`). The server
//   renumbers to 1..N, audits every changed rank and answers with the
//   committed list.
//
// One submission keeps ONE `device_event_id` until it resolves —
// including a manual Retry after an unknown outcome; the server replays
// a committed change (`created: false`) instead of applying it twice.
// A new submission generates a fresh key (`newDeviceEventId`). There
// is no automatic transport retry (api/client.ts has none). A stale
// precondition answers 409 with the current entries attached;
// `hotListChanged` extracts them.
//
// Wire responses are the backend's snake_case schemas; this module
// maps them to the camelCase model the view renders.
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequest, apiRequestWithStatus } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type HotListAction =
  'ADD' | 'REMOVE' | 'MOVE_UP' | 'MOVE_DOWN' | 'DRAG' | 'UNDO' | 'REDO';

/** One position of the PN's ACTIVE quantity in the Department. */
export interface HotListLocation {
  area: { id: number; name: string; color: string | null };
  machine: { id: number; name: string } | null;
  /** External Operation name, as on the Production Board. */
  activity: string | null;
  state: 'MACHINE' | 'QUEUE' | 'PROCESSING' | 'DONE';
  quantity: number;
}

export interface HotListEntry {
  workOrderDemandId: number;
  /** 1 = highest; null only in candidate lists. */
  rank: number | null;
  partNumber: string;
  workOrderId: number;
  /** Verbatim external number, or null on an internal Work Order —
   * rendered as `—` with its internal label (display-only). */
  workOrderNumber: string | null;
  /** ISO `YYYY-MM-DD`. */
  workOrderReceivedDate: string;
  workOrderCompleted: boolean;
  requestType: 'NEW' | 'MODIFY';
  jobNumbers: string[];
  requestedQuantity: number;
  allocatedQuantity: number;
  shortageQuantity: number;
  /** Open Work Order with a shortage left (PROJECT_PROFILE §14). */
  active: boolean;
  releasedQuantity: number;
  /** ISO `YYYY-MM-DD`, or null. */
  dueDate: string | null;
  /** PN-level: every demand of the same PN carries the same list. */
  partNumberLocations: HotListLocation[];
}

export interface HotList {
  department: { id: number; name: string };
  entries: HotListEntry[];
}

export interface HotListCandidates {
  /** The scanned PN (barcode lookups), else null. */
  partNumber: string | null;
  candidates: HotListEntry[];
  /** Matching demand already on the Hot list, in any state. */
  alreadyListedCount: number;
  /** True when a search result was cut at the server's limit. */
  truncated: boolean;
}

export interface HotListChangeInput {
  /** The submission's idempotency key — reused verbatim on a Retry. */
  deviceEventId: string;
  action: HotListAction;
  /** Demand ids in the rank order the user confirmed against. */
  expectedOrder: number[];
  newOrder: number[];
}

export interface HotListRankChange {
  workOrderDemandId: number;
  partNumber: string;
  workOrderNumber: string | null;
  previousRank: number | null;
  newRank: number | null;
}

export interface HotListChangeResult {
  /** True for a fresh 201 commit, false for an idempotent replay. */
  created: boolean;
  deviceEventId: string;
  action: HotListAction;
  changes: HotListRankChange[];
  /** The list as committed (201), or the current list (replay). */
  entries: HotListEntry[];
}

// ---------------------------------------------------------------------------
// Wire
// ---------------------------------------------------------------------------

interface HotListLocationWire {
  area: { id: number; name: string; color: string | null };
  machine: { id: number; name: string } | null;
  activity: string | null;
  state: HotListLocation['state'];
  quantity: number;
}

interface HotListEntryWire {
  work_order_demand_id: number;
  rank: number | null;
  part_number: string;
  work_order_id: number;
  work_order_number: string | null;
  work_order_received_date: string;
  work_order_completed: boolean;
  request_type: 'NEW' | 'MODIFY';
  job_numbers: string[];
  requested_quantity: number;
  allocated_quantity: number;
  shortage_quantity: number;
  active: boolean;
  released_quantity: number;
  due_date: string | null;
  part_number_locations: HotListLocationWire[];
}

interface HotListWire {
  department: { id: number; name: string };
  entries: HotListEntryWire[];
}

interface HotListCandidatesWire {
  part_number: string | null;
  candidates: HotListEntryWire[];
  already_listed_count: number;
  truncated: boolean;
}

interface HotListChangeResultWire {
  device_event_id: string;
  action: HotListAction;
  created: boolean;
  changes: {
    work_order_demand_id: number;
    part_number: string;
    work_order_number: string | null;
    previous_rank: number | null;
    new_rank: number | null;
  }[];
  entries: HotListEntryWire[];
}

function toLocation(wire: HotListLocationWire): HotListLocation {
  return {
    area: wire.area,
    machine: wire.machine,
    activity: wire.activity,
    state: wire.state,
    quantity: wire.quantity,
  };
}

function toEntry(wire: HotListEntryWire): HotListEntry {
  return {
    workOrderDemandId: wire.work_order_demand_id,
    rank: wire.rank,
    partNumber: wire.part_number,
    workOrderId: wire.work_order_id,
    workOrderNumber: wire.work_order_number,
    workOrderReceivedDate: wire.work_order_received_date,
    workOrderCompleted: wire.work_order_completed,
    requestType: wire.request_type,
    jobNumbers: wire.job_numbers,
    requestedQuantity: wire.requested_quantity,
    allocatedQuantity: wire.allocated_quantity,
    shortageQuantity: wire.shortage_quantity,
    active: wire.active,
    releasedQuantity: wire.released_quantity,
    dueDate: wire.due_date,
    partNumberLocations: wire.part_number_locations.map(toLocation),
  };
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

/**
 * Load the Hot list of the single active Department. The server refuses
 * a missing (404) or ambiguous (409, several active Departments)
 * configuration with an operator message.
 */
export async function getHotList(): Promise<HotList> {
  const wire = await apiRequest<HotListWire>('/api/hot-list');
  return { department: wire.department, entries: wire.entries.map(toEntry) };
}

/**
 * The eligible demand to add: by free-text `search` (PN, Work Order
 * Number or Job Number — bounded, `truncated` says so), by a scanned
 * PN `barcode` (every eligible demand of that PN, unbounded), or —
 * with neither — every eligible demand (bounded).
 */
export async function getHotListCandidates(
  query: { search: string } | { barcode: string } | Record<string, never>,
): Promise<HotListCandidates> {
  const params = new URLSearchParams();
  if ('search' in query) params.set('search', query.search);
  if ('barcode' in query) params.set('barcode', query.barcode);
  const encoded = params.toString();
  const suffix = encoded ? `?${encoded}` : '';
  const wire = await apiRequest<HotListCandidatesWire>(
    `/api/hot-list/candidates${suffix}`,
  );
  return {
    partNumber: wire.part_number,
    candidates: wire.candidates.map(toEntry),
    alreadyListedCount: wire.already_listed_count,
    truncated: wire.truncated,
  };
}

/** Apply ONE confirmed Hot list change (or replay its committed result). */
export async function applyHotListChange(
  input: HotListChangeInput,
): Promise<HotListChangeResult> {
  const { status, data: wire } =
    await apiRequestWithStatus<HotListChangeResultWire>(
      '/api/hot-list/changes',
      {
        method: 'POST',
        body: {
          device_event_id: input.deviceEventId,
          action: input.action,
          expected_order: input.expectedOrder,
          new_order: input.newOrder,
        },
      },
    );
  return {
    created: status === 201,
    deviceEventId: wire.device_event_id,
    action: wire.action,
    changes: wire.changes.map((change) => ({
      workOrderDemandId: change.work_order_demand_id,
      partNumber: change.part_number,
      workOrderNumber: change.work_order_number,
      previousRank: change.previous_rank,
      newRank: change.new_rank,
    })),
    entries: wire.entries.map(toEntry),
  };
}

/**
 * The current entries of a change refused because the list was changed
 * elsewhere (409 with `hot_list_changed: true`), or null when the error
 * is anything else. Nothing was written by that refusal.
 */
export function hotListChanged(error: unknown): HotListEntry[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body;
  if (!body || typeof body !== 'object') return null;
  const record = body as { hot_list_changed?: unknown; entries?: unknown };
  if (record.hot_list_changed !== true) return null;
  if (!Array.isArray(record.entries)) return null;
  return (record.entries as HotListEntryWire[]).map(toEntry);
}
