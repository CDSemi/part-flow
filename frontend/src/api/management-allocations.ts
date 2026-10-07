// Management allocation API (Phase 14 slice 5 — PROJECT_PROFILE §8.12,
// §18; GUI_DESIGN §11.6).
//
// The Management side of Work Order Allocation, all three writes
// authorized by Edit Work Order Allocation and idempotent per
// `device_event_id`: a routine allocation of stocked quantity left for
// later (never beyond the line's remaining demand), the authorized
// beyond-demand correction (its own command: one demand line, more
// than its remaining demand, never above the available stocked
// quantity, mandatory reason), and the reversal of one allocation row.
// One read serves the dialog: the allocation context of a PN (its open
// Work Orders) or of one demand line (whatever its Work Order state),
// with the stock figures and every active allocation row with the
// User who recorded it.
//
// These calls are signed-in Management requests: they never carry a
// Scan Station's enrolled-device header.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, apiRequestWithStatus } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** The User who recorded an allocation row (a history reference — the
 * User may have been deactivated since). */
export interface AllocationUserRef {
  id: number;
  displayName: string;
  /** Avatar cache version (ISO 8601); null when there is no avatar. */
  avatarUpdatedAt: string | null;
}

/** One active allocation row of a demand line (not reversed, not a
 * reversal). */
export interface ContextAllocation {
  allocationId: number;
  quantity: number;
  source: 'STOCKROOM' | 'MANAGEMENT';
  isManualOverride: boolean;
  /** Recorded by the authorized beyond-demand correction. */
  exceedsDemand: boolean;
  allocationReason: string | null;
  stationId: string | null;
  allocatedAt: string;
  /** null for a station row and for a row recorded before sign-in. */
  actorUser: AllocationUserRef | null;
}

/** One demand line with its derived allocation figures. */
export interface ContextLine {
  workOrderId: number;
  workOrderNumber: string | null;
  workOrderCompleted: boolean;
  receivedDate: string;
  workOrderDemandId: number;
  requestType: 'NEW' | 'MODIFY';
  dueDate: string | null;
  priorityRank: number | null;
  requestedQuantity: number;
  /** Derived from the active allocation rows. */
  allocatedQuantity: number;
  /** max(requested − allocated, 0). */
  remainingShortage: number;
  /** max(allocated − requested, 0). */
  beyondDemandQuantity: number;
  /** Oldest first. */
  activeAllocations: ContextAllocation[];
}

export interface AllocationContext {
  partNumber: string;
  stockedQuantity: number;
  activeAllocatedQuantity: number;
  availableStockedQuantity: number;
  /** Canonical demand order. */
  lines: ContextLine[];
}

/** A PN (its open Work Orders) or one demand line (any state). */
export type AllocationScope =
  { partNumber: string } | { workOrderDemandId: number };

/** The committed answer of one Management allocation command. */
export interface ManagementAllocationResult {
  kind: 'ALLOCATE' | 'REVERSE_ALLOCATION' | 'ALLOCATE_BEYOND_DEMAND';
  partNumber: string;
  /** The quantity allocated (or, on a reversal, taken back). */
  allocationQuantity: number;
  completedWorkOrderIds: number[];
  reopenedWorkOrderIds: number[];
  deviceEventId: string;
  /** false when the server replayed an already committed command. */
  created: boolean;
}

// ---------------------------------------------------------------------------
// Wire mapping
// ---------------------------------------------------------------------------

interface ContextAllocationWire {
  allocation_id: number;
  quantity: number;
  source: 'STOCKROOM' | 'MANAGEMENT';
  is_manual_override: boolean;
  exceeds_demand: boolean;
  allocation_reason: string | null;
  station_id: string | null;
  allocated_at: string;
  actor_user: unknown;
}

interface ContextLineWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_completed: boolean;
  received_date: string;
  work_order_demand_id: number;
  request_type: 'NEW' | 'MODIFY';
  due_date: string | null;
  priority_rank: number | null;
  requested_quantity: number;
  allocated_quantity: number;
  remaining_shortage: number;
  beyond_demand_quantity: number;
  active_allocations: ContextAllocationWire[];
}

interface AllocationContextWire {
  part_number: string;
  stocked_quantity: number;
  active_allocated_quantity: number;
  available_stocked_quantity: number;
  lines: ContextLineWire[];
}

interface AllocationResultWire {
  kind: string;
  part_number: string;
  allocation_quantity: number;
  completed_work_order_ids: number[];
  reopened_work_order_ids: number[];
  device_event_id: string;
}

const RESULT_KINDS: readonly ManagementAllocationResult['kind'][] = [
  'ALLOCATE',
  'REVERSE_ALLOCATION',
  'ALLOCATE_BEYOND_DEMAND',
];

/**
 * The `actor_user` reference of an allocation row: null (or absent)
 * when no User recorded it; a malformed reference throws instead of
 * rendering a wrong name.
 */
export function toAllocationUserRef(wire: unknown): AllocationUserRef | null {
  if (wire === undefined || wire === null) return null;
  if (typeof wire !== 'object' || Array.isArray(wire)) throw malformedActor();
  const { id, display_name, avatar_updated_at } = wire as Record<
    string,
    unknown
  >;
  if (
    typeof id !== 'number' ||
    typeof display_name !== 'string' ||
    (avatar_updated_at !== null && typeof avatar_updated_at !== 'string')
  ) {
    throw malformedActor();
  }
  return { id, displayName: display_name, avatarUpdatedAt: avatar_updated_at };
}

function malformedActor(): Error {
  return new Error('The server answered a malformed allocation actor.');
}

function toContextAllocation(wire: ContextAllocationWire): ContextAllocation {
  return {
    allocationId: wire.allocation_id,
    quantity: wire.quantity,
    source: wire.source,
    isManualOverride: wire.is_manual_override,
    exceedsDemand: wire.exceeds_demand,
    allocationReason: wire.allocation_reason,
    stationId: wire.station_id,
    allocatedAt: wire.allocated_at,
    actorUser: toAllocationUserRef(wire.actor_user),
  };
}

function toContextLine(wire: ContextLineWire): ContextLine {
  return {
    workOrderId: wire.work_order_id,
    workOrderNumber: wire.work_order_number,
    workOrderCompleted: wire.work_order_completed,
    receivedDate: wire.received_date,
    workOrderDemandId: wire.work_order_demand_id,
    requestType: wire.request_type,
    dueDate: wire.due_date,
    priorityRank: wire.priority_rank,
    requestedQuantity: wire.requested_quantity,
    allocatedQuantity: wire.allocated_quantity,
    remainingShortage: wire.remaining_shortage,
    beyondDemandQuantity: wire.beyond_demand_quantity,
    activeAllocations: wire.active_allocations.map(toContextAllocation),
  };
}

function toResult(
  status: number,
  wire: AllocationResultWire,
): ManagementAllocationResult {
  const kind = RESULT_KINDS.find((known) => known === wire.kind);
  if (kind === undefined) {
    throw new Error('The server answered an unknown allocation command.');
  }
  return {
    kind,
    partNumber: wire.part_number,
    allocationQuantity: wire.allocation_quantity,
    completedWorkOrderIds: wire.completed_work_order_ids,
    reopenedWorkOrderIds: wire.reopened_work_order_ids,
    deviceEventId: wire.device_event_id,
    created: status === 201,
  };
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

/**
 * The allocation context of a PN (every demand line of its open Work
 * Orders, fully and over-allocated lines included) or of one demand
 * line whatever its Work Order state. A read — an advisory snapshot;
 * every write is judged again by the server.
 */
export async function getAllocationContext(
  scope: AllocationScope,
): Promise<AllocationContext> {
  const params =
    'partNumber' in scope
      ? new URLSearchParams({ part_number: scope.partNumber })
      : new URLSearchParams({
          work_order_demand_id: String(scope.workOrderDemandId),
        });
  const wire = await apiRequest<AllocationContextWire>(
    `/api/allocations/management/context?${params.toString()}`,
  );
  return {
    partNumber: wire.part_number,
    stockedQuantity: wire.stocked_quantity,
    activeAllocatedQuantity: wire.active_allocated_quantity,
    availableStockedQuantity: wire.available_stocked_quantity,
    lines: wire.lines.map(toContextLine),
  };
}

/**
 * Allocate stocked quantity to one demand line — never beyond its
 * remaining demand (the server refuses it, nothing recorded). Resolves
 * only when the server confirmed the write (201 fresh, 200 replay).
 */
export async function allocateFromStock(input: {
  partNumber: string;
  workOrderDemandId: number;
  quantity: number;
  note: string | null;
  deviceEventId: string;
}): Promise<ManagementAllocationResult> {
  const { status, data } = await apiRequestWithStatus<AllocationResultWire>(
    '/api/allocations/management',
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        allocation_quantity: input.quantity,
        lines: [
          {
            work_order_demand_id: input.workOrderDemandId,
            quantity: input.quantity,
          },
        ],
        ...(input.note !== null ? { reason: input.note } : {}),
        device_event_id: input.deviceEventId,
      },
    },
  );
  return toResult(status, data);
}

/**
 * Record the authorized beyond-demand correction of one demand line:
 * more than its remaining demand, never above the available stocked
 * quantity, with the mandatory reason.
 */
export async function allocateBeyondDemand(input: {
  partNumber: string;
  workOrderDemandId: number;
  quantity: number;
  reason: string;
  deviceEventId: string;
}): Promise<ManagementAllocationResult> {
  const { status, data } = await apiRequestWithStatus<AllocationResultWire>(
    '/api/allocations/corrections',
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        work_order_demand_id: input.workOrderDemandId,
        quantity: input.quantity,
        reason: input.reason,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return toResult(status, data);
}

/** Reverse one allocation row with the mandatory reason: its quantity
 * returns to the available stocked quantity. */
export async function reverseAllocation(input: {
  allocationId: number;
  reason: string;
  deviceEventId: string;
}): Promise<ManagementAllocationResult> {
  const { status, data } = await apiRequestWithStatus<AllocationResultWire>(
    `/api/allocations/${input.allocationId}/reversals`,
    {
      method: 'POST',
      body: { reason: input.reason, device_event_id: input.deviceEventId },
    },
  );
  return toResult(status, data);
}
