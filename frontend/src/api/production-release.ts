// Explicit production release API (Phase 4 — GUI_DESIGN §11.4).
//
// The one write that creates production quantity. It is addressed
// through the initiating WorkOrderDemand (the UI always releases from
// a saved demand row) and protected by a client-generated
// `device_event_id` idempotency key: ONE submission keeps ONE key
// through every transport retry — a replay of the committed release
// returns the original result (`created: false`) and creates nothing.
// A new submission (a reopened dialog, a release after a completed
// one) generates a fresh key.
//
// The backend's confirmation-required outcome (the PN already has
// active quantity, SLICE1 §8.2) arrives as a 409 whose body carries
// the existing active distribution; `activeQuantityConfirmation`
// extracts it so the dialog can show the distribution and require
// explicit confirmation — resubmitting with `confirmActiveQuantity`
// and the SAME `device_event_id` (nothing was created by the 409).
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequestWithStatus } from './client';

export interface ProductionReleaseInput {
  partNumber: string;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  /** Required for PLANNED, absent for FLOATING. */
  routeTemplateId: number | null;
  startingAreaId: number;
  operationId: number;
  /** Set only after the existing distribution was shown (§11.4). */
  confirmActiveQuantity: boolean;
  /** The submission's idempotency key — reused verbatim on retries. */
  deviceEventId: string;
}

export interface ProductionReleaseResult {
  /** True for a fresh 201 commit, false for an idempotent replay. */
  created: boolean;
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  /** The AssignedRoute snapshot id (PLANNED), null for FLOATING. */
  assignedRouteId: number | null;
  startingAreaId: number;
  operationId: number;
  /** The appended RECEIVED Movement. */
  movementId: number;
  deviceEventId: string;
  occurredAt: string;
}

/** One existing ACTIVE flow of the PN, as shown for confirmation. */
export interface ActiveQuantityEntry {
  quantityFlowId: number;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  currentAreaId: number;
  currentAreaName: string;
}

interface ProductionReleaseWire {
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  route_mode: 'FLOATING' | 'PLANNED';
  assigned_route_id: number | null;
  starting_area_id: number;
  operation_id: number;
  movement_id: number;
  device_event_id: string;
  occurred_at: string;
}

/**
 * A fresh UUID idempotency key for one release submission.
 *
 * `crypto.randomUUID` is secure-context only, so it is absent when the
 * stack is served over plain HTTP from anything but `localhost` — a
 * realistic shop-floor setup. The RFC 4122 v4 fallback keeps the
 * release flow working there instead of throwing while the dialog
 * renders; `crypto.getRandomValues` needs no secure context.
 */
export function newDeviceEventId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(bytes, (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-');
}

export async function releaseToProduction(
  workOrderId: number,
  demandId: number,
  input: ProductionReleaseInput,
): Promise<ProductionReleaseResult> {
  const { status, data: wire } =
    await apiRequestWithStatus<ProductionReleaseWire>(
      `/api/work-orders/${workOrderId}/demands/${demandId}/release`,
      {
        method: 'POST',
        body: {
          part_number: input.partNumber,
          quantity: input.quantity,
          route_mode: input.routeMode,
          route_template_id: input.routeTemplateId,
          starting_area_id: input.startingAreaId,
          operation_id: input.operationId,
          confirm_active_quantity: input.confirmActiveQuantity,
          device_event_id: input.deviceEventId,
        },
      },
    );
  return {
    created: status === 201,
    quantityFlowId: wire.quantity_flow_id,
    partNumber: wire.part_number,
    quantity: wire.quantity,
    routeMode: wire.route_mode,
    assignedRouteId: wire.assigned_route_id,
    startingAreaId: wire.starting_area_id,
    operationId: wire.operation_id,
    movementId: wire.movement_id,
    deviceEventId: wire.device_event_id,
    occurredAt: wire.occurred_at,
  };
}

/**
 * The active-distribution confirmation payload of a failed release,
 * or null when the error is anything else.
 */
export function activeQuantityConfirmation(
  error: unknown,
): ActiveQuantityEntry[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body;
  if (!body || typeof body !== 'object') return null;
  const record = body as {
    confirmation_required?: unknown;
    existing_active_quantity?: unknown;
  };
  if (record.confirmation_required !== true) return null;
  if (!Array.isArray(record.existing_active_quantity)) return null;
  return record.existing_active_quantity.map((entry) => {
    const wire = entry as {
      quantity_flow_id: number;
      quantity: number;
      route_mode: 'FLOATING' | 'PLANNED';
      current_area_id: number;
      current_area_name: string;
    };
    return {
      quantityFlowId: wire.quantity_flow_id,
      quantity: wire.quantity,
      routeMode: wire.route_mode,
      currentAreaId: wire.current_area_id,
      currentAreaName: wire.current_area_name,
    };
  });
}
