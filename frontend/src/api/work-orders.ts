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
  /** Server-owned stored status (`OPEN` in Phase 4). */
  status: string;
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
   * read-only; this is never a client-session guess.
   */
  hasReleasedQuantity: boolean;
}

export interface WorkOrderDetail {
  id: number;
  workOrderNumber: string | null;
  receivedDate: string;
  dueDate: string | null;
  status: string;
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
}

interface WorkOrderDetailWire {
  id: number;
  work_order_number: string | null;
  received_date: string;
  due_date: string | null;
  status: string;
  demands: WorkOrderDemandWire[];
}

function toSummary(wire: WorkOrderSummaryWire): WorkOrderSummary {
  return {
    id: wire.id,
    workOrderNumber: wire.work_order_number,
    receivedDate: wire.received_date,
    dueDate: wire.due_date,
    status: wire.status,
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
  };
}

function toDetail(wire: WorkOrderDetailWire): WorkOrderDetail {
  return {
    id: wire.id,
    workOrderNumber: wire.work_order_number,
    receivedDate: wire.received_date,
    dueDate: wire.due_date,
    status: wire.status,
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

/** The active WO list, newest first. */
export async function listWorkOrders(): Promise<WorkOrderSummary[]> {
  const wires = await apiRequest<WorkOrderSummaryWire[]>('/api/work-orders');
  return wires.map(toSummary);
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
