// Work Order intake API (Phase 4 — GUI_DESIGN §11.1–§11.3).
//
// Business demand only: listing/searching the WO list, loading Work
// Order Details with demand lines, creating a Work Order with its
// demand draft (one Save = one transaction), saving edits, and
// removing a saved demand line. Production release lives in
// `src/api/production-release.ts` — nothing here can create
// production quantity.
//
// Wire responses are the backend's snake_case schemas; this module
// maps them to the camelCase application types the views render, and
// back. Server-owned fields (`status`, `priority_rank`,
// `allocated_quantity`) are read-only here — the backend rejects any
// attempt to submit them.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WorkOrderSummary {
  id: number;
  /** Verbatim external number, or null on an internal Work Order —
   * rendered as `—` (display-only, never persisted). */
  workOrderNumber: string | null;
  /** ISO `YYYY-MM-DD`. */
  receivedDate: string;
  /** ISO `YYYY-MM-DD`, or null while the Work Order is unscheduled. */
  dueDate: string | null;
  /** Server-derived read status: `OPEN`, `RELEASED`, or `COMPLETED`
   * once every demand line is fully allocated (Phase 10). */
  status: string;
  /** The done date (`completed_at`, ISO timestamp) — set exactly while
   * the Work Order is completed; a later allocation reversal clears it
   * and returns the Work Order to the active list. */
  completedAt: string | null;
  demandLineCount: number;
  /** Canonical PNs of the demand lines, in demand order. */
  partNumbers: string[];
}

export interface WorkOrderDemand {
  id: number;
  workOrderId: number;
  /** The canonical uppercase PN — identity, kept verbatim. */
  partNumber: string;
  requestType: 'NEW' | 'MODIFY';
  requestedQuantity: number;
  /** Server-owned (Phase 10 allocation). */
  allocatedQuantity: number;
  dueDate: string | null;
  /** Server-owned (Phase 12 Hot ranking). */
  priorityRank: number | null;
  jobNumbers: string[];
  requester: string | null;
  reason: string | null;
  notes: string | null;
  /**
   * Server-derived release evidence (immutable RECEIVED Movement
   * context): true once any production quantity was released for this
   * demand — in any session, ever. The line renders Released and
   * accepts the restricted edit only (Qty never below the released or
   * allocated quantity, due date, Job Numbers — PROJECT_PROFILE §13);
   * this is never a client-session guess.
   */
  hasReleasedQuantity: boolean;
  /**
   * Quantity already released for this demand, and what is left of the
   * requested quantity. A demand may be released in several parts (20
   * of 50, then 12, then 18), so the release action stays offered
   * while `remainingQuantity > 0` — the server enforces the same cap.
   * Both are derived from Movement history: never stored, never a
   * session guess.
   */
  releasedQuantity: number;
  remainingQuantity: number;
}

export interface WorkOrderDetail {
  id: number;
  workOrderNumber: string | null;
  receivedDate: string;
  dueDate: string | null;
  status: string;
  /** The done date — see `WorkOrderSummary.completedAt`. */
  completedAt: string | null;
  demands: WorkOrderDemand[];
}

/** One new demand line of a save (Add Part flow, GUI_DESIGN §11.3). */
export interface NewDemandLine {
  partNumber: string;
  requestedQuantity: number;
  requestType: 'NEW' | 'MODIFY';
  dueDate: string | null;
  jobNumbers: string[];
  notes: string | null;
}

/**
 * A partial edit of one saved demand line. Only present fields travel:
 * an omitted field keeps its saved value, while an explicit null
 * `dueDate` is the valid "No due date" choice. The PN is deliberately
 * absent — a different PN is a new line, never a rewrite.
 */
export interface DemandLineEdit {
  id: number;
  requestType?: 'NEW' | 'MODIFY';
  requestedQuantity?: number;
  dueDate?: string | null;
  jobNumbers?: string[];
  notes?: string | null;
}

// ---------------------------------------------------------------------------
// Wire mapping
// ---------------------------------------------------------------------------

interface WorkOrderSummaryWire {
  id: number;
  work_order_number: string | null;
  received_date: string;
  due_date: string | null;
  status: string;
  completed_at: string | null;
  demand_line_count: number;
  part_numbers: string[];
}

interface WorkOrderDemandWire {
  id: number;
  work_order_id: number;
  part_number: string;
  request_type: 'NEW' | 'MODIFY';
  requested_quantity: number;
  allocated_quantity: number;
  due_date: string | null;
  priority_rank: number | null;
  job_numbers: string[];
  requester: string | null;
  reason: string | null;
  notes: string | null;
  has_released_quantity: boolean;
  released_quantity: number;
  remaining_quantity: number;
}

interface WorkOrderDetailWire {
  id: number;
  work_order_number: string | null;
  received_date: string;
  due_date: string | null;
  status: string;
  completed_at: string | null;
  demands: WorkOrderDemandWire[];
}

function toSummary(wire: WorkOrderSummaryWire): WorkOrderSummary {
  return {
    id: wire.id,
    workOrderNumber: wire.work_order_number,
    receivedDate: wire.received_date,
    dueDate: wire.due_date,
    status: wire.status,
    completedAt: wire.completed_at,
    demandLineCount: wire.demand_line_count,
    partNumbers: wire.part_numbers,
  };
}

function toDemand(wire: WorkOrderDemandWire): WorkOrderDemand {
  return {
    id: wire.id,
    workOrderId: wire.work_order_id,
    partNumber: wire.part_number,
    requestType: wire.request_type,
    requestedQuantity: wire.requested_quantity,
    allocatedQuantity: wire.allocated_quantity,
    dueDate: wire.due_date,
    priorityRank: wire.priority_rank,
    jobNumbers: wire.job_numbers,
    requester: wire.requester,
    reason: wire.reason,
    notes: wire.notes,
    hasReleasedQuantity: wire.has_released_quantity,
    releasedQuantity: wire.released_quantity,
    remainingQuantity: wire.remaining_quantity,
  };
}

function toDetail(wire: WorkOrderDetailWire): WorkOrderDetail {
  return {
    id: wire.id,
    workOrderNumber: wire.work_order_number,
    receivedDate: wire.received_date,
    dueDate: wire.due_date,
    status: wire.status,
    completedAt: wire.completed_at,
    demands: wire.demands.map(toDemand),
  };
}

function newLineBody(line: NewDemandLine): Record<string, unknown> {
  return {
    part_number: line.partNumber,
    requested_quantity: line.requestedQuantity,
    request_type: line.requestType,
    due_date: line.dueDate,
    job_numbers: line.jobNumbers,
    notes: line.notes,
  };
}

function lineEditBody(edit: DemandLineEdit): Record<string, unknown> {
  return {
    id: edit.id,
    ...(edit.requestType !== undefined
      ? { request_type: edit.requestType }
      : {}),
    ...(edit.requestedQuantity !== undefined
      ? { requested_quantity: edit.requestedQuantity }
      : {}),
    ...('dueDate' in edit ? { due_date: edit.dueDate } : {}),
    ...(edit.jobNumbers !== undefined ? { job_numbers: edit.jobNumbers } : {}),
    ...('notes' in edit ? { notes: edit.notes } : {}),
  };
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

/**
 * Most Work Orders one active-list read returns — the SERVER's bound
 * (`work_orders.LIST_RESULT_LIMIT`), mirrored here only to word the
 * "refine your search" hint. Never a client-side slice.
 */
export const WORK_ORDER_LIST_LIMIT = 100;

/**
 * The active WO list, newest first — filtered AND bounded by the server.
 *
 * `search` travels as `?search=`, so the contains-match over the Work
 * Order Number (GUI_DESIGN §11.1) runs in the database and a match
 * outside the first page is still found. Nothing leaves the active
 * list before allocation-derived completion (Phase 10), so the client
 * never downloads the whole list in order to filter it locally.
 */
export async function listWorkOrders(
  search?: string,
): Promise<WorkOrderSummary[]> {
  const term = search?.trim() ?? '';
  const query = term ? `?search=${encodeURIComponent(term)}` : '';
  const wires = await apiRequest<WorkOrderSummaryWire[]>(
    `/api/work-orders${query}`,
  );
  return wires.map(toSummary);
}

/** The due outcome filter of the completed history (GUI_DESIGN §11.5). */
export type DueOutcome = 'ALL' | 'ON_TIME' | 'LATE' | 'NO_DUE_DATE';

/** Keyset position over `(completed_at, id)` — the server's cursor. */
export interface CompletedCursor {
  completedAt: string;
  id: number;
}

export interface CompletedWorkOrdersPage {
  workOrders: WorkOrderSummary[];
  /** Matching completed Work Orders in the WHOLE history. */
  total: number;
  /** Completed Work Orders in the whole history regardless of the
   * filters — tells "none ever" from "none in this range". */
  historyTotal: number;
  /** Continue with this cursor for the next page; null = no more. */
  nextCursor: CompletedCursor | null;
}

/** Rows one page of the completed history returns (the server's page
 * size, `work_orders.COMPLETED_PAGE_LIMIT`); `Show more` continues. */
export const COMPLETED_PAGE_LIMIT = 50;

interface CompletedWorkOrdersPageWire {
  work_orders: WorkOrderSummaryWire[];
  total: number;
  history_total: number;
  next_cursor_completed_at: string | null;
  next_cursor_id: number | null;
}

/**
 * The read-only completed history (GUI_DESIGN §11.5), newest done date
 * first. Search (WO Number, PN, Job Number), the done-date range, the
 * due outcome and the keyset paging all run on the SERVER — the history
 * is unbounded by design and never downloaded whole.
 */
export async function listCompletedWorkOrders(input: {
  search?: string;
  /** ISO timestamps: inclusive lower / exclusive upper `completed_at`. */
  doneFrom?: string | null;
  doneTo?: string | null;
  dueOutcome?: DueOutcome;
  cursor?: CompletedCursor | null;
}): Promise<CompletedWorkOrdersPage> {
  const params = new URLSearchParams();
  const term = input.search?.trim() ?? '';
  if (term) params.set('search', term);
  if (input.doneFrom) params.set('done_from', input.doneFrom);
  if (input.doneTo) params.set('done_to', input.doneTo);
  if (input.dueOutcome && input.dueOutcome !== 'ALL') {
    params.set('due_outcome', input.dueOutcome);
  }
  if (input.cursor) {
    params.set('cursor_completed_at', input.cursor.completedAt);
    params.set('cursor_id', String(input.cursor.id));
  }
  params.set('limit', String(COMPLETED_PAGE_LIMIT));
  const wire = await apiRequest<CompletedWorkOrdersPageWire>(
    `/api/work-orders/completed?${params.toString()}`,
  );
  return {
    workOrders: wire.work_orders.map(toSummary),
    total: wire.total,
    historyTotal: wire.history_total,
    nextCursor:
      wire.next_cursor_completed_at !== null && wire.next_cursor_id !== null
        ? {
            completedAt: wire.next_cursor_completed_at,
            id: wire.next_cursor_id,
          }
        : null,
  };
}

/**
 * Exact-resolution lookup of one entered WO Number (verbatim equality
 * — stored numbers are verbatim). The New Work Order dialog uses this
 * to open an existing number instead of duplicating it; null = free.
 */
export async function resolveWorkOrderNumber(
  workOrderNumber: string,
): Promise<WorkOrderSummary | null> {
  const wires = await apiRequest<WorkOrderSummaryWire[]>(
    `/api/work-orders?number=${encodeURIComponent(workOrderNumber)}`,
  );
  return wires.length > 0 ? toSummary(wires[0]) : null;
}

export async function getWorkOrder(id: number): Promise<WorkOrderDetail> {
  const wire = await apiRequest<WorkOrderDetailWire>(`/api/work-orders/${id}`);
  return toDetail(wire);
}

/** One Save = one transaction: header plus all lines (≥ 1 required). */
export async function createWorkOrder(input: {
  /** null persists NULL (internal Work Order); entered text verbatim. */
  workOrderNumber: string | null;
  receivedDate: string;
  dueDate: string | null;
  lines: NewDemandLine[];
}): Promise<WorkOrderDetail> {
  const wire = await apiRequest<WorkOrderDetailWire>('/api/work-orders', {
    method: 'POST',
    body: {
      work_order_number: input.workOrderNumber,
      received_date: input.receivedDate,
      due_date: input.dueDate,
      lines: input.lines.map(newLineBody),
    },
  });
  return toDetail(wire);
}

/** The Work Order Details Save demand — header edits + line edits +
 * new lines in one all-or-nothing transaction. */
export async function updateWorkOrder(
  id: number,
  input: {
    /**
     * Omit to keep the current number. A string travels VERBATIM (the
     * audited Work Order Number edit, PROJECT_PROFILE §7 — e.g. adding
     * the real external number to an internal Work Order); an explicit
     * null persists NULL. Never trimmed, reformatted, or padded here.
     */
    workOrderNumber?: string | null;
    /** Omit to keep; explicit null persists NULL (audited edit). */
    dueDate?: string | null;
    lineEdits: DemandLineEdit[];
    newLines: NewDemandLine[];
  },
): Promise<WorkOrderDetail> {
  const wire = await apiRequest<WorkOrderDetailWire>(`/api/work-orders/${id}`, {
    method: 'PATCH',
    body: {
      ...('workOrderNumber' in input
        ? { work_order_number: input.workOrderNumber }
        : {}),
      ...('dueDate' in input ? { due_date: input.dueDate } : {}),
      line_edits: input.lineEdits.map(lineEditBody),
      new_lines: input.newLines.map(newLineBody),
    },
  });
  return toDetail(wire);
}

/**
 * Remove one saved demand line. The backend enforces the canonical
 * rules (PROJECT_PROFILE §13, §8.2): released demand and the last
 * demand line of a Work Order answer 409 and remove nothing.
 */
export async function deleteWorkOrderDemand(
  workOrderId: number,
  demandId: number,
): Promise<void> {
  await apiRequest<void>(
    `/api/work-orders/${workOrderId}/demands/${demandId}`,
    {
      method: 'DELETE',
    },
  );
}
