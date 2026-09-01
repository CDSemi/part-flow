// Work Order Allocation API (Phase 10 — PROJECT_PROFILE §8.12, §18;
// GUI_DESIGN §10).
//
// The receiving confirmation that follows a `STOCKED` arrival at the
// Stockroom station: the server's canonical allocation suggestion (a
// read), and the confirmed allocation (one command, idempotent per
// `device_event_id`). Allocation is a record of its own — it never
// references a Movement or a Quantity Flow and never changes the
// Movement history.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, apiRequestWithStatus } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One outstanding demand line in the canonical demand ordering. */
export interface SuggestedAllocationLine {
  workOrderId: number;
  workOrderNumber: string | null;
  receivedDate: string;
  workOrderDemandId: number;
  priorityRank: number | null;
  dueDate: string | null;
  requestedQuantity: number;
  previouslyAllocatedQuantity: number;
  remainingShortage: number;
  /** The server's proposal for THIS line (0 when the quantity does not
   * reach it) — the operator may adjust it before confirming. */
  proposedQuantity: number;
}

export interface AllocationSuggestion {
  partNumber: string;
  /** The quantity the suggestion was computed for — capped by the
   * server at the available stocked quantity. */
  quantity: number;
  stockedQuantity: number;
  activeAllocatedQuantity: number;
  availableStockedQuantity: number;
  proposedTotal: number;
  /** Quantity no outstanding demand can take — it stays in stock. */
  unallocatedQuantity: number;
  /** Every outstanding line, canonical order, proposed-0 lines included. */
  lines: SuggestedAllocationLine[];
}

export interface AllocationLine {
  workOrderDemandId: number;
  quantity: number;
}

export interface AllocationInput {
  partNumber: string;
  /**
   * The explicit quantity being allocated — at the Stockroom the
   * just-stocked quantity the operator confirmed, never the PN's whole
   * available stock. The lines must sum to exactly it (the server
   * refuses anything else with nothing written), and the server judges
   * it against the available stocked quantity under its lock: a figure
   * that shrank since the dialog opened is refused, nothing written.
   */
  allocationQuantity: number;
  lines: AllocationLine[];
  /** The Stockroom station confirming the receiving allocation. */
  stationId: string | null;
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface AllocationRow {
  allocationId: number;
  workOrderDemandId: number;
  workOrderId: number;
  quantity: number;
  isManualOverride: boolean;
}

export interface AllocationResult {
  partNumber: string;
  allocationQuantity: number;
  rows: AllocationRow[];
  /** Work Orders this confirmation completed — every demand line fully
   * allocated (server-derived). */
  completedWorkOrderIds: number[];
  deviceEventId: string;
  /** false when the server replayed an already committed allocation. */
  created: boolean;
}

// ---------------------------------------------------------------------------
// Wire mapping
// ---------------------------------------------------------------------------

interface SuggestedLineWire {
  work_order_id: number;
  work_order_number: string | null;
  received_date: string;
  work_order_demand_id: number;
  priority_rank: number | null;
  due_date: string | null;
  requested_quantity: number;
  previously_allocated_quantity: number;
  remaining_shortage: number;
  proposed_quantity: number;
}

interface SuggestionWire {
  part_number: string;
  quantity: number;
  stocked_quantity: number;
  active_allocated_quantity: number;
  available_stocked_quantity: number;
  proposed_total: number;
  unallocated_quantity: number;
  lines: SuggestedLineWire[];
}

interface AllocationRowWire {
  allocation_id: number;
  work_order_demand_id: number;
  work_order_id: number;
  quantity: number;
  is_manual_override: boolean;
}

interface AllocationResultWire {
  part_number: string;
  allocation_quantity: number;
  rows: AllocationRowWire[];
  completed_work_order_ids: number[];
  device_event_id: string;
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

/**
 * The canonical allocation suggestion for a PN — a read, nothing is
 * recorded. `quantity` is the quantity about to be allocated (the
 * just-stocked quantity); omitted, the server suggests for the whole
 * available stock.
 */
export async function getAllocationSuggestion(
  partNumber: string,
  quantity?: number,
): Promise<AllocationSuggestion> {
  const params = new URLSearchParams({ part_number: partNumber });
  if (quantity !== undefined) params.set('quantity', String(quantity));
  const wire = await apiRequest<SuggestionWire>(
    `/api/allocations/suggestion?${params.toString()}`,
  );
  return {
    partNumber: wire.part_number,
    quantity: wire.quantity,
    stockedQuantity: wire.stocked_quantity,
    activeAllocatedQuantity: wire.active_allocated_quantity,
    availableStockedQuantity: wire.available_stocked_quantity,
    proposedTotal: wire.proposed_total,
    unallocatedQuantity: wire.unallocated_quantity,
    lines: wire.lines.map((line) => ({
      workOrderId: line.work_order_id,
      workOrderNumber: line.work_order_number,
      receivedDate: line.received_date,
      workOrderDemandId: line.work_order_demand_id,
      priorityRank: line.priority_rank,
      dueDate: line.due_date,
      requestedQuantity: line.requested_quantity,
      previouslyAllocatedQuantity: line.previously_allocated_quantity,
      remainingShortage: line.remaining_shortage,
      proposedQuantity: line.proposed_quantity,
    })),
  };
}

/**
 * Record the confirmed allocation. Resolves ONLY when the server
 * confirmed the write: 201 fresh, 200 on an idempotent replay of the
 * same `deviceEventId` + same intent. Every refusal — lines not adding
 * up to the allocation quantity, a line beyond its remaining shortage,
 * an allocation quantity the available stock no longer covers — is an
 * `ApiError` and nothing was recorded.
 */
export async function confirmAllocation(
  input: AllocationInput,
): Promise<AllocationResult> {
  const { status, data } = await apiRequestWithStatus<AllocationResultWire>(
    '/api/allocations',
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        allocation_quantity: input.allocationQuantity,
        lines: input.lines.map((line) => ({
          work_order_demand_id: line.workOrderDemandId,
          quantity: line.quantity,
        })),
        ...(input.stationId !== null ? { station_id: input.stationId } : {}),
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    partNumber: data.part_number,
    allocationQuantity: data.allocation_quantity,
    rows: data.rows.map((row) => ({
      allocationId: row.allocation_id,
      workOrderDemandId: row.work_order_demand_id,
      workOrderId: row.work_order_id,
      quantity: row.quantity,
      isManualOverride: row.is_manual_override,
    })),
    completedWorkOrderIds: data.completed_work_order_ids,
    deviceEventId: data.device_event_id,
    created: status === 201,
  };
}
