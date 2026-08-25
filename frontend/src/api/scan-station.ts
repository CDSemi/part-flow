// Scan Station API client (Phase 5 — GUI_DESIGN §4; PROJECT_PROFILE §15).
//
// The read models a Scan Station loads (station context, PN scan
// resolution, Area inventory) and the ONE command the station records
// in this phase: the transfer of a whole Quantity Flow into the
// station's Area. Wire shapes are the backend's snake_case; the
// exported types are the camelCase the views use. No business rules
// live here — source/route/Operation resolution and the transaction
// are the backend's (app/application/transfers.py); this module only
// carries the confirmed intent across and reads the typed outcomes.
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequest, apiRequestWithStatus } from './client';

// ---------------------------------------------------------------------------
// Shared shapes
// ---------------------------------------------------------------------------

export interface AreaRef {
  id: number;
  name: string;
  color: string | null;
  description: string | null;
  isTerminal: boolean;
}

export interface OperationRef {
  id: number;
  code: string;
  name: string | null;
  isExternal: boolean;
}

/** The initiating Work Order Demand of a flow (recap context only). */
export interface WorkOrderContext {
  workOrderId: number;
  /** null for an internal blank-number Work Order — rendered `—`. */
  workOrderNumber: string | null;
  workOrderDemandId: number;
  requestType: 'NEW' | 'MODIFY';
}

export interface FlowInArea {
  quantityFlowId: number;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  workOrder: WorkOrderContext | null;
}

interface AreaRefWire {
  id: number;
  name: string;
  color: string | null;
  description: string | null;
  is_terminal: boolean;
}

interface OperationRefWire {
  id: number;
  code: string;
  name: string | null;
  is_external: boolean;
}

interface WorkOrderContextWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_demand_id: number;
  request_type: 'NEW' | 'MODIFY';
}

interface FlowInAreaWire {
  quantity_flow_id: number;
  quantity: number;
  route_mode: 'FLOATING' | 'PLANNED';
  work_order: WorkOrderContextWire | null;
}

function toAreaRef(wire: AreaRefWire): AreaRef {
  return {
    id: wire.id,
    name: wire.name,
    color: wire.color,
    description: wire.description,
    isTerminal: wire.is_terminal,
  };
}

function toOperationRef(wire: OperationRefWire): OperationRef {
  return {
    id: wire.id,
    code: wire.code,
    name: wire.name,
    isExternal: wire.is_external,
  };
}

function toWorkOrderContext(
  wire: WorkOrderContextWire | null,
): WorkOrderContext | null {
  if (!wire) return null;
  return {
    workOrderId: wire.work_order_id,
    workOrderNumber: wire.work_order_number,
    workOrderDemandId: wire.work_order_demand_id,
    requestType: wire.request_type,
  };
}

function toFlowInArea(wire: FlowInAreaWire): FlowInArea {
  return {
    quantityFlowId: wire.quantity_flow_id,
    quantity: wire.quantity,
    routeMode: wire.route_mode,
    workOrder: toWorkOrderContext(wire.work_order),
  };
}

/** CSS color of an Area reference (fallback for Areas without one). */
export function areaRefColor(area: Pick<AreaRef, 'color'> | null): string {
  return area?.color ?? 'var(--faint)';
}

// ---------------------------------------------------------------------------
// Station context
// ---------------------------------------------------------------------------

export interface StationContext {
  stationId: string;
  department: { id: number; name: string };
  area: AreaRef;
  /** Active Operations of the bound Area, in stable code order. */
  operations: OperationRef[];
  /** Header/statistics mode: an Area with Machines queues quantity. */
  hasMachines: boolean;
}

interface StationContextWire {
  station_id: string;
  department: { id: number; name: string };
  area: AreaRefWire;
  operations: OperationRefWire[];
  has_machines: boolean;
}

export async function getStationContext(
  stationId: string,
): Promise<StationContext> {
  const wire = await apiRequest<StationContextWire>(
    `/api/scan-stations/${encodeURIComponent(stationId)}/context`,
  );
  return {
    stationId: wire.station_id,
    department: wire.department,
    area: toAreaRef(wire.area),
    operations: wire.operations.map(toOperationRef),
    hasMachines: wire.has_machines,
  };
}

// ---------------------------------------------------------------------------
// PN scan resolution
// ---------------------------------------------------------------------------

export type RouteStatus = 'FLOATING' | 'ON_ROUTE' | 'DEVIATION';

/** One explicit transfer source — never ranked, never combined. */
export interface TransferCandidate {
  quantityFlowId: number;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  currentArea: AreaRef;
  routeStatus: RouteStatus;
  expectedNextArea: AreaRef | null;
  /** The Operation the Planned Route expects at its next step. */
  expectedOperationId: number | null;
  /** The Operation the destination resolves to without a choice; null
   * means the operator must choose one of the Area's Operations. */
  suggestedOperationId: number | null;
  workOrder: WorkOrderContext | null;
}

export type ScanResolutionKind =
  'ALREADY_IN_AREA' | 'TRANSFER_SOURCE_AVAILABLE' | 'NO_TRANSFERABLE_QUANTITY';

export interface ScanResolution {
  partNumber: string;
  stationId: string;
  area: AreaRef;
  resolution: ScanResolutionKind;
  inArea: FlowInArea[];
  candidates: TransferCandidate[];
  operations: OperationRef[];
  hasActiveDemand: boolean;
  /** Set when the station's Area can never receive a transfer. */
  transferBlockedReason: string | null;
}

interface TransferCandidateWire {
  quantity_flow_id: number;
  quantity: number;
  route_mode: 'FLOATING' | 'PLANNED';
  current_area: AreaRefWire;
  route_status: RouteStatus;
  expected_next_area: AreaRefWire | null;
  expected_operation_id: number | null;
  suggested_operation_id: number | null;
  work_order: WorkOrderContextWire | null;
}

interface ScanResolutionWire {
  part_number: string;
  station_id: string;
  area: AreaRefWire;
  resolution: ScanResolutionKind;
  in_area: FlowInAreaWire[];
  candidates: TransferCandidateWire[];
  operations: OperationRefWire[];
  has_active_demand: boolean;
  transfer_blocked_reason: string | null;
}

/**
 * Resolve a scanned PN barcode (`PF:PN:…`, verbatim scanner value) or a
 * manually entered PN at a station. A read — nothing is recorded.
 */
export async function resolveScan(
  stationId: string,
  input: { barcode: string } | { partNumber: string },
): Promise<ScanResolution> {
  const wire = await apiRequest<ScanResolutionWire>(
    `/api/scan-stations/${encodeURIComponent(stationId)}/scans/resolve`,
    {
      method: 'POST',
      body:
        'barcode' in input
          ? { barcode: input.barcode }
          : { part_number: input.partNumber },
    },
  );
  return {
    partNumber: wire.part_number,
    stationId: wire.station_id,
    area: toAreaRef(wire.area),
    resolution: wire.resolution,
    inArea: wire.in_area.map(toFlowInArea),
    candidates: wire.candidates.map((candidate) => ({
      quantityFlowId: candidate.quantity_flow_id,
      quantity: candidate.quantity,
      routeMode: candidate.route_mode,
      currentArea: toAreaRef(candidate.current_area),
      routeStatus: candidate.route_status,
      expectedNextArea: candidate.expected_next_area
        ? toAreaRef(candidate.expected_next_area)
        : null,
      expectedOperationId: candidate.expected_operation_id,
      suggestedOperationId: candidate.suggested_operation_id,
      workOrder: toWorkOrderContext(candidate.work_order),
    })),
    operations: wire.operations.map(toOperationRef),
    hasActiveDemand: wire.has_active_demand,
    transferBlockedReason: wire.transfer_blocked_reason,
  };
}

// ---------------------------------------------------------------------------
// Transfer command
// ---------------------------------------------------------------------------

export interface TransferInput {
  stationId: string;
  partNumber: string;
  /** The ONE source the operator selected, and the Area it was shown in. */
  quantityFlowId: number;
  sourceAreaId: number;
  /** The destination Area the operator confirmed (station binding
   * precondition — the backend refuses a rebound station). */
  targetAreaId: number;
  /** Phase 5: always the flow's whole quantity. */
  quantity: number;
  /** null when the destination resolves the Operation itself. */
  operationId: number | null;
  confirmRouteDeviation: boolean;
  routeDeviationReason: string | null;
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface RouteDeviation {
  kind: 'AREA' | 'OPERATION';
  expectedNextAreaId: number | null;
  expectedOperationId: number | null;
  actualAreaId: number;
  actualOperationId: number | null;
  reason?: string;
}

export interface TransferResult {
  movementId: number;
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  fromAreaId: number;
  toAreaId: number;
  operationId: number;
  stationId: string;
  assignedRouteStepId: number | null;
  routeDeviation: RouteDeviation | null;
  deviceEventId: string;
  occurredAt: string;
  /** false when the server replayed an already committed transfer. */
  created: boolean;
}

interface RouteDeviationWire {
  kind: 'AREA' | 'OPERATION';
  expected_next_area_id: number | null;
  expected_operation_id: number | null;
  actual_area_id: number;
  actual_operation_id: number | null;
  reason?: string;
}

interface TransferResultWire {
  movement_id: number;
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  from_area_id: number;
  to_area_id: number;
  operation_id: number;
  station_id: string;
  assigned_route_step_id: number | null;
  route_deviation: RouteDeviationWire | null;
  device_event_id: string;
  occurred_at: string;
}

function toRouteDeviation(wire: RouteDeviationWire): RouteDeviation {
  return {
    kind: wire.kind,
    expectedNextAreaId: wire.expected_next_area_id,
    expectedOperationId: wire.expected_operation_id,
    actualAreaId: wire.actual_area_id,
    actualOperationId: wire.actual_operation_id,
    reason: wire.reason,
  };
}

/**
 * Record the confirmed transfer. Resolves ONLY when the server
 * confirmed the write: 201 for a fresh transfer, 200 for an idempotent
 * replay of the same `deviceEventId` + same intent (a retry after an
 * unknown outcome). Every rejection — partial quantity, a station
 * rebound since the confirmation, an Operation deactivated meanwhile,
 * a route deviation not yet confirmed — is an `ApiError` and nothing
 * was recorded.
 */
export async function transferToStationArea(
  input: TransferInput,
): Promise<TransferResult> {
  const { status, data } = await apiRequestWithStatus<TransferResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/transfers`,
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        quantity_flow_id: input.quantityFlowId,
        source_area_id: input.sourceAreaId,
        target_area_id: input.targetAreaId,
        quantity: input.quantity,
        operation_id: input.operationId,
        confirm_route_deviation: input.confirmRouteDeviation,
        route_deviation_reason: input.routeDeviationReason,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    movementId: data.movement_id,
    quantityFlowId: data.quantity_flow_id,
    partNumber: data.part_number,
    quantity: data.quantity,
    fromAreaId: data.from_area_id,
    toAreaId: data.to_area_id,
    operationId: data.operation_id,
    stationId: data.station_id,
    assignedRouteStepId: data.assigned_route_step_id,
    routeDeviation: data.route_deviation
      ? toRouteDeviation(data.route_deviation)
      : null,
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}

/**
 * The route deviation a refused transfer asks the operator to confirm
 * (the backend's 409 with `confirmation_required`), or null for any
 * other failure.
 */
export function routeDeviationConfirmation(
  error: unknown,
): RouteDeviation | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body;
  if (!body || typeof body !== 'object') return null;
  const record = body as {
    confirmation_required?: unknown;
    route_deviation?: unknown;
  };
  if (record.confirmation_required !== true) return null;
  const deviation = record.route_deviation;
  if (!deviation || typeof deviation !== 'object') return null;
  return toRouteDeviation(deviation as RouteDeviationWire);
}

// ---------------------------------------------------------------------------
// Area inventory
// ---------------------------------------------------------------------------

export interface InventoryLine {
  partNumber: string;
  totalQuantity: number;
  flows: FlowInArea[];
}

export interface AreaInventory {
  area: AreaRef;
  lines: InventoryLine[];
  totalPartNumbers: number;
  totalQuantity: number;
}

interface AreaInventoryWire {
  area: AreaRefWire;
  lines: {
    part_number: string;
    total_quantity: number;
    flows: FlowInAreaWire[];
  }[];
  total_part_numbers: number;
  total_quantity: number;
}

export async function getAreaInventory(areaId: number): Promise<AreaInventory> {
  const wire = await apiRequest<AreaInventoryWire>(
    `/api/areas/${areaId}/inventory`,
  );
  return {
    area: toAreaRef(wire.area),
    lines: wire.lines.map((line) => ({
      partNumber: line.part_number,
      totalQuantity: line.total_quantity,
      flows: line.flows.map(toFlowInArea),
    })),
    totalPartNumbers: wire.total_part_numbers,
    totalQuantity: wire.total_quantity,
  };
}
