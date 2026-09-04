// Scan Station API client (Phase 5/6 — GUI_DESIGN §4; PROJECT_PROFILE
// §12, §15).
//
// The read models a Scan Station loads (station context, PN scan
// resolution, Machine scan resolution, Area inventory, the Phase 9
// undo preview) and the production commands the station records: the
// transfer of a Quantity Flow into the station's Area (Phase 5 —
// completing ON_MACHINE quantity implicitly since Phase 6; carrying
// the explicit Repair intent since Phase 9), the Phase 6 one-shot
// Machine-Area actions: assign to a Machine, QUEUE (return to the Area
// queue) and DONE (complete Area processing), the Phase 8 `Combine
// quantities` (the explicit merge of the server-judged combinable
// flows of one PN), and the Phase 9 corrections: Scrap (one auditable
// SCRAPPED operation per confirmation), the quantity addition
// (`QUANTITY_ADJUSTED · INCREASE` on a NEW Quantity Flow) and the
// command-level Undo (compensating REVERSED Movements for one complete
// committed command, previewed through `undo-preview` before anything
// is submitted).
// Since Phase 8 every command accepts a quantity smaller than the flow's:
// the server splits the flow first inside the same command and reports
// the consumed source and the remainder; the client never splits.
// Wire shapes are the backend's snake_case; the exported types are the
// camelCase the views use. No business rules live here — state, Machine
// and route resolution and every transaction are the backend's
// (app/application/transfers.py, machine_processing.py,
// quantity_events.py, undo.py); this module only carries the confirmed
// intent across and reads the typed outcomes.
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequest, apiRequestWithStatus } from './client';
// The shared Area monitoring model lives in ./area-inventory — the ONE
// client model the Scan Station and the Area Board both render. It is
// re-exported here so the station modules keep importing their whole
// API surface from this facade.
import type {
  AreaInventory,
  AreaRef,
  AreaRefWire,
  FlowAction,
  FlowInArea,
  FlowInAreaWire,
  InventoryLine,
  MachineInventory,
  MachineOperationalState,
  MachineRef,
  MachineRefWire,
  OperationRef,
  OperationRefWire,
  ProcessingState,
  RecordedOperation,
  WorkOrderContext,
  WorkOrderContextWire,
} from './area-inventory';
import {
  toAreaRef,
  toFlowInArea,
  toMachineRef,
  toOperationRef,
  toWorkOrderContext,
} from './area-inventory';

export type {
  AreaInventory,
  AreaRef,
  FlowAction,
  FlowInArea,
  InventoryLine,
  MachineInventory,
  MachineOperationalState,
  MachineRef,
  OperationRef,
  ProcessingState,
  RecordedOperation,
  WorkOrderContext,
};
export { areaRefColor, getAreaInventory } from './area-inventory';

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
  /** ON_MACHINE and PROCESSING quantity is completed implicitly by the
   * transfer (AREA_COMPLETED + TRANSFERRED in one command). */
  processingState: ProcessingState;
  machineId: number | null;
  routeStatus: RouteStatus;
  expectedNextArea: AreaRef | null;
  /** The Operation the Planned Route expects at its next step. */
  expectedOperationId: number | null;
  /** The Operation the destination resolves to without a choice; null
   * means the operator must choose one of the Area's Operations. */
  suggestedOperationId: number | null;
  /** Phase 9: the station's Area was previously visited by this
   * quantity — only then may `Return quantity for repair` be offered.
   * Never inferred by the client; the server judges the history. */
  repairAvailable: boolean;
  workOrder: WorkOrderContext | null;
}

export type ScanResolutionKind =
  'ALREADY_IN_AREA' | 'TRANSFER_SOURCE_AVAILABLE' | 'NO_TRANSFERABLE_QUANTITY';

/**
 * One internal blank-number MODIFY Work Order a `Receive Quantity`
 * receipt may reuse (PROJECT_PROFILE §14). Presented by its business
 * facts — the ids travel with the request only, because an internal
 * key is never the user-facing Work Order identifier (§7).
 */
export interface InternalWorkOrder {
  workOrderId: number;
  workOrderDemandId: number;
  receivedDate: string;
  dueDate: string | null;
  /** The quantity its existing demand line for this PN requests. */
  requestedQuantity: number;
  jobNumbers: string[];
}

interface InternalWorkOrderWire {
  work_order_id: number;
  work_order_demand_id: number;
  received_date: string;
  due_date: string | null;
  requested_quantity: number;
  job_numbers: string[];
}

function toInternalWorkOrder(wire: InternalWorkOrderWire): InternalWorkOrder {
  return {
    workOrderId: wire.work_order_id,
    workOrderDemandId: wire.work_order_demand_id,
    receivedDate: wire.received_date,
    dueDate: wire.due_date,
    requestedQuantity: wire.requested_quantity,
    jobNumbers: wire.job_numbers,
  };
}

/**
 * One existing ACTIVE quantity of the PN, as the `Receive Quantity`
 * confirmation shows it (PROJECT_PROFILE §14). The same shape arrives
 * with the resolution and with the server's confirmation-required
 * refusal, so one renderer serves both.
 */
export interface ActiveQuantityEntry {
  quantityFlowId: number;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  currentAreaId: number;
  currentAreaName: string;
}

interface ActiveQuantityWire {
  quantity_flow_id: number;
  quantity: number;
  route_mode: 'FLOATING' | 'PLANNED';
  current_area_id: number;
  current_area_name: string;
}

function toActiveQuantity(wire: ActiveQuantityWire): ActiveQuantityEntry {
  return {
    quantityFlowId: wire.quantity_flow_id,
    quantity: wire.quantity,
    routeMode: wire.route_mode,
    currentAreaId: wire.current_area_id,
    currentAreaName: wire.current_area_name,
  };
}

export interface ScanResolution {
  partNumber: string;
  stationId: string;
  area: AreaRef;
  resolution: ScanResolutionKind;
  inArea: FlowInArea[];
  candidates: TransferCandidate[];
  operations: OperationRef[];
  /** Phase 10.5: a demand line of the PN still has a business shortage
   * (`requested_quantity > allocated_quantity`), so its quantity
   * belongs to a production release, not to a station receipt. */
  hasActiveDemand: boolean;
  /** Phase 10.5: the station may INTRODUCE this PN here — no active
   * demand and an Area that can start production (GUI_DESIGN §4.7
   * item 1). Existing active quantity does NOT withhold it: it makes
   * the receipt an explicitly confirmed separate quantity. */
  intakeAvailable: boolean;
  /** The PartNumber master already exists (the Step 1 copy tells a
   * known PN from a new one). */
  partNumberKnown: boolean;
  /** The internal blank-number MODIFY Work Orders a MODIFY receipt may
   * reuse; more than one REQUIRES an explicit selection and the client
   * never picks one (PROJECT_PROFILE §14). */
  internalWorkOrders: InternalWorkOrder[];
  /** Phase 10.5 (PROJECT_PROFILE §14): the PN's existing ACTIVE
   * distribution, wherever it is. A receipt never joins it, so while
   * this is non-empty `Receive Quantity` shows it and requires the
   * operator's explicit confirmation that the receipt creates a
   * SEPARATE quantity; the server judges the same rule at write
   * time. `Combine quantities` stays the only way to merge. */
  activeQuantity: ActiveQuantityEntry[];
  /** Set when the station's Area can never receive a transfer. */
  transferBlockedReason: string | null;
  /** Several flows match: the operator selects exactly one. */
  requiresSelection: boolean;
  /** Phase 8: the groups (flow ids, at least two each) of in-Area flows
   * the SERVER judges combinable — one identical production context
   * per group. The station offers `Combine quantities` for exactly
   * these; it never judges compatibility itself. */
  combineGroups: number[][];
  /** Phase 9: the PN's total scrapped quantity, net of reversed scraps. */
  scrappedQuantity: number;
  /** Phase 10: the PN's stocked quantity and the part of it no
   * allocation has taken yet — both derived by the server from the
   * STOCKED history and the allocation records. */
  stockedQuantity: number;
  availableStockedQuantity: number;
  /** Phase 10: the station is bound to a terminal (Stockroom) Area and
   * the candidates are the sources the operator STOCKS from — the
   * arrival there is the `STOCKED` command, never a transfer. */
  stockAvailable: boolean;
  /** Phase 10.5: the SERVER instant this scan resolved (ISO 8601 with
   * offset). `Receive Quantity` carries it through every wizard step
   * and sends it back with the confirmed receipt, because the received
   * date follows the SCAN and not the confirmation (PROJECT_PROFILE
   * §14) — a wizard open across midnight still records the scan day.
   * The station never reads its own clock for it. */
  scannedAt: string;
}

interface TransferCandidateWire {
  quantity_flow_id: number;
  quantity: number;
  route_mode: 'FLOATING' | 'PLANNED';
  current_area: AreaRefWire;
  processing_state: ProcessingState;
  machine_id: number | null;
  route_status: RouteStatus;
  expected_next_area: AreaRefWire | null;
  expected_operation_id: number | null;
  suggested_operation_id: number | null;
  repair_available: boolean;
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
  intake_available: boolean;
  part_number_known: boolean;
  internal_work_orders: InternalWorkOrderWire[];
  active_quantity: ActiveQuantityWire[];
  transfer_blocked_reason: string | null;
  requires_selection: boolean;
  combine_groups: number[][];
  scrapped_quantity: number;
  stocked_quantity: number;
  available_stocked_quantity: number;
  stock_available: boolean;
  scanned_at: string;
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
      processingState: candidate.processing_state,
      machineId: candidate.machine_id,
      routeStatus: candidate.route_status,
      expectedNextArea: candidate.expected_next_area
        ? toAreaRef(candidate.expected_next_area)
        : null,
      expectedOperationId: candidate.expected_operation_id,
      suggestedOperationId: candidate.suggested_operation_id,
      repairAvailable: candidate.repair_available,
      workOrder: toWorkOrderContext(candidate.work_order),
    })),
    operations: wire.operations.map(toOperationRef),
    hasActiveDemand: wire.has_active_demand,
    intakeAvailable: wire.intake_available,
    partNumberKnown: wire.part_number_known,
    internalWorkOrders: wire.internal_work_orders.map(toInternalWorkOrder),
    activeQuantity: wire.active_quantity.map(toActiveQuantity),
    transferBlockedReason: wire.transfer_blocked_reason,
    requiresSelection: wire.requires_selection,
    combineGroups: wire.combine_groups,
    scrappedQuantity: wire.scrapped_quantity,
    stockedQuantity: wire.stocked_quantity,
    availableStockedQuantity: wire.available_stocked_quantity,
    stockAvailable: wire.stock_available,
    scannedAt: wire.scanned_at,
  };
}

// ---------------------------------------------------------------------------
// Machine scan resolution — the one-shot assignment context (Phase 6)
// ---------------------------------------------------------------------------

export interface MachineScanResolution {
  stationId: string;
  area: AreaRef;
  /** The Machine preselected for the one-shot assignment. */
  machine: MachineRef;
  assignedQuantity: number;
  /** Every QUEUED flow of the station's Area — never picked. */
  queued: FlowInArea[];
  requiresSelection: boolean;
}

interface MachineScanResolutionWire {
  station_id: string;
  area: AreaRefWire;
  machine: MachineRefWire;
  assigned_quantity: number;
  queued: FlowInAreaWire[];
  requires_selection: boolean;
}

/**
 * Resolve a scanned Machine barcode (`PF:MACHINE:<asset-tag>`, verbatim
 * scanner value) or a manually entered Asset Tag at a station into the
 * one-shot Assign to Machine context. A read — nothing is recorded and
 * nothing is remembered server-side: an unknown Asset Tag is 404, a
 * retired, other-Area or maintenance Machine a 409 `ApiError`.
 */
export async function resolveMachineScan(
  stationId: string,
  input: { barcode: string } | { assetTag: string },
): Promise<MachineScanResolution> {
  const wire = await apiRequest<MachineScanResolutionWire>(
    `/api/scan-stations/${encodeURIComponent(stationId)}/machine-scans/resolve`,
    {
      method: 'POST',
      body:
        'barcode' in input
          ? { barcode: input.barcode }
          : { asset_tag: input.assetTag },
    },
  );
  return {
    stationId: wire.station_id,
    area: toAreaRef(wire.area),
    machine: toMachineRef(wire.machine),
    assignedQuantity: wire.assigned_quantity,
    queued: wire.queued.map(toFlowInArea),
    requiresSelection: wire.requires_selection,
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
  /** The confirmed quantity: the whole flow, or a part of it (Phase 8 —
   * the server splits the flow first inside the same command). */
  quantity: number;
  /** null when the destination resolves the Operation itself. */
  operationId: number | null;
  confirmRouteDeviation: boolean;
  routeDeviationReason: string | null;
  /** Phase 9: the explicit `Return quantity for repair` intent — the
   * same transfer command recorded with the Repair movement intent and
   * its mandatory reason. Never inferred: the operator chose Repair. */
  repair: boolean;
  repairReason: string | null;
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
  /** `TRANSFERRED`, or `STOCKED` for the Stockroom arrival (Phase 10)
   * — the same command shape recorded with a different meaning. */
  movementType: 'TRANSFERRED' | 'STOCKED';
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  fromAreaId: number;
  toAreaId: number;
  operationId: number;
  stationId: string;
  assignedRouteStepId: number | null;
  routeDeviation: RouteDeviation | null;
  /** The implicit AREA_COMPLETED of the same command when the quantity
   * was still actively processing at the source: the Movement id, and
   * the Machine it left (ON_MACHINE, Phase 6) — null for directly
   * processing quantity (Phase 7). Both null for queued or finished
   * quantity. */
  completedMovementId: number | null;
  completedMachineId: number | null;
  /** Phase 8 — set when only a part of the source moved: the consumed
   * source flow, and the remainder flow left at the source in the
   * source's state with its quantity. All null for a whole-flow
   * transfer. `quantityFlowId` is then the moved child. */
  sourceQuantityFlowId: number | null;
  remainderQuantityFlowId: number | null;
  remainderQuantity: number | null;
  /** Phase 9 — set for a Repair: the typed intent recorded on the
   * TRANSFERRED row (`REPAIR`) and its mandatory reason. Both null for
   * a plain transfer. */
  movementReason: string | null;
  reason: string | null;
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
  movement_type: 'TRANSFERRED' | 'STOCKED';
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  from_area_id: number;
  to_area_id: number;
  operation_id: number;
  station_id: string;
  assigned_route_step_id: number | null;
  route_deviation: RouteDeviationWire | null;
  completed_movement_id: number | null;
  completed_machine_id: number | null;
  source_quantity_flow_id: number | null;
  remainder_quantity_flow_id: number | null;
  remainder_quantity: number | null;
  movement_reason: string | null;
  reason: string | null;
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
 * unknown outcome). Every rejection — a quantity exceeding the source,
 * a station rebound since the confirmation, an Operation deactivated meanwhile,
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
        repair: input.repair,
        repair_reason: input.repairReason,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return toTransferResult(data, status);
}

/** The Stockroom arrival (Phase 10): the transfer's shape without the
 * Repair intent — quantity stocked at a terminal Area is
 * manufacturing-complete and never returned for repair by this command. */
export type StockInput = Omit<TransferInput, 'repair' | 'repairReason'>;

/**
 * Record the confirmed `STOCKED` arrival of ONE Quantity Flow — whole,
 * or a part of it (the server splits first) — at the station's terminal
 * Area (PROJECT_PROFILE §18). Resolves ONLY when the server confirmed
 * the write: 201 fresh, 200 on an idempotent replay of the same
 * `deviceEventId` + same intent. Every rejection — a non-terminal Area,
 * a stale source, an exceeding quantity, an unconfirmed route deviation
 * — is an `ApiError` and nothing was recorded. The flow closes as
 * stocked; the receiving allocation that follows is its own command
 * (`src/api/allocations.ts`).
 */
export async function stockAtStationArea(
  input: StockInput,
): Promise<TransferResult> {
  const { status, data } = await apiRequestWithStatus<TransferResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/stockings`,
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
  return toTransferResult(data, status);
}

function toTransferResult(
  data: TransferResultWire,
  status: number,
): TransferResult {
  return {
    movementId: data.movement_id,
    movementType: data.movement_type,
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
    completedMovementId: data.completed_movement_id,
    completedMachineId: data.completed_machine_id,
    sourceQuantityFlowId: data.source_quantity_flow_id,
    remainderQuantityFlowId: data.remainder_quantity_flow_id,
    remainderQuantity: data.remainder_quantity,
    movementReason: data.movement_reason,
    reason: data.reason,
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}

/**
 * Whether a failed production-write submission leaves the outcome
 * UNKNOWN.
 *
 * A production write is only "not recorded" when the server itself
 * rejected it before writing — an application/business rejection (4xx
 * other than a timeout). A transport failure (no HTTP response at all),
 * a request timeout (408) or any 5xx proves nothing: the backend may
 * have COMMITTED and failed while producing the response, or a reverse
 * proxy may answer 502/504 after the upstream committed. Those must be
 * presented as an unknown outcome and retried with the same
 * `device_event_id`, never as "nothing was changed".
 */
export function writeOutcomeUnknown(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 408 || error.status >= 500;
}

/** The transfer's name for the shared rule above. */
export const transferOutcomeUnknown = writeOutcomeUnknown;

// ---------------------------------------------------------------------------
// Machine-Area processing commands (Phase 6)
// ---------------------------------------------------------------------------

export type MachineActionKind = 'ASSIGN' | 'QUEUE' | 'DONE';

export interface MachineActionInput {
  stationId: string;
  partNumber: string;
  /** The ONE flow the operator selected. */
  quantityFlowId: number;
  /** ASSIGN: the Machine to assign to. QUEUE/DONE: the Machine the
   * quantity is on — an optimistic precondition the server checks.
   * DONE only: null for the direct-processing completion of an Area
   * without Machines (Phase 7) — the request carries no Machine. */
  machineId: number | null;
  /** The confirmed quantity: the whole flow, or a part of it (Phase 8 —
   * the server splits the flow first inside the same command). */
  quantity: number;
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface MachineActionResult {
  movementId: number;
  movementType:
    'ASSIGNED_TO_MACHINE' | 'RELEASED_FROM_MACHINE' | 'AREA_COMPLETED';
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  areaId: number;
  /** null for a direct-processing completion (no Machine involved). */
  machineId: number | null;
  operationId: number;
  stationId: string;
  processingState: ProcessingState;
  /** Phase 8 — set when only a part of the flow was acted on: the
   * consumed source flow and the remainder flow that kept the source's
   * state, with its quantity. All null for a whole-flow action;
   * `quantityFlowId` is then the acted-on child. */
  sourceQuantityFlowId: number | null;
  remainderQuantityFlowId: number | null;
  remainderQuantity: number | null;
  deviceEventId: string;
  occurredAt: string;
  /** false when the server replayed an already committed action. */
  created: boolean;
}

interface MachineActionResultWire {
  movement_id: number;
  movement_type: MachineActionResult['movementType'];
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  area_id: number;
  machine_id: number | null;
  operation_id: number;
  station_id: string;
  processing_state: ProcessingState;
  source_quantity_flow_id: number | null;
  remainder_quantity_flow_id: number | null;
  remainder_quantity: number | null;
  device_event_id: string;
  occurred_at: string;
}

const MACHINE_ACTION_PATH: Record<MachineActionKind, string> = {
  ASSIGN: 'machine-assignments',
  QUEUE: 'machine-releases',
  DONE: 'area-completions',
};

/**
 * Record one confirmed in-Area action. Resolves ONLY when the server
 * confirmed the write: 201 fresh, 200 for an idempotent replay of the
 * same `deviceEventId` + same intent. Every rejection — a quantity
 * exceeding the flow, a flow that moved or changed state meanwhile, a retired,
 * other-Area or maintenance Machine, a stale Machine precondition, a
 * Machine-less DONE on quantity in an Area with Machines — is an
 * `ApiError` and nothing was recorded. A DONE with `machineId: null`
 * is the direct-processing completion (Phase 7): the request omits
 * `machine_id`, so the server records an `AREA_COMPLETED` without a
 * Machine — the same endpoint, a distinct intent.
 */
export async function recordMachineAction(
  kind: MachineActionKind,
  input: MachineActionInput,
): Promise<MachineActionResult> {
  const { status, data } = await apiRequestWithStatus<MachineActionResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/${MACHINE_ACTION_PATH[kind]}`,
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        quantity_flow_id: input.quantityFlowId,
        ...(input.machineId === null ? {} : { machine_id: input.machineId }),
        quantity: input.quantity,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    movementId: data.movement_id,
    movementType: data.movement_type,
    quantityFlowId: data.quantity_flow_id,
    partNumber: data.part_number,
    quantity: data.quantity,
    areaId: data.area_id,
    machineId: data.machine_id,
    operationId: data.operation_id,
    stationId: data.station_id,
    processingState: data.processing_state,
    sourceQuantityFlowId: data.source_quantity_flow_id,
    remainderQuantityFlowId: data.remainder_quantity_flow_id,
    remainderQuantity: data.remainder_quantity,
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}

// ---------------------------------------------------------------------------
// Combine quantities — the explicit merge (Phase 8)
// ---------------------------------------------------------------------------

export interface CombineInput {
  stationId: string;
  partNumber: string;
  /** The flows the operator selected (at least two) from one
   * server-reported combinable group. */
  quantityFlowIds: number[];
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface CombineResult {
  movementId: number;
  /** The resulting flow. */
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  areaId: number;
  machineId: number | null;
  operationId: number;
  stationId: string;
  processingState: ProcessingState;
  /** The consumed sources, ascending id. */
  sourceQuantityFlowIds: number[];
  deviceEventId: string;
  occurredAt: string;
  /** false when the server replayed an already committed combine. */
  created: boolean;
}

interface CombineResultWire {
  movement_id: number;
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  area_id: number;
  machine_id: number | null;
  operation_id: number;
  station_id: string;
  processing_state: ProcessingState;
  source_quantity_flow_ids: number[];
  device_event_id: string;
  occurred_at: string;
}

/**
 * Combine the selected quantities of one PN in the station's Area into
 * one (the backend's MERGED command). Resolves ONLY when the server
 * confirmed the write: 201 fresh, 200 for an idempotent replay of the
 * same `deviceEventId` + same selection. Every rejection — a flow that
 * moved, changed state or was consumed meanwhile, a production context
 * the server no longer judges identical — is an `ApiError` and nothing
 * was recorded. Nothing is ever combined automatically.
 */
export async function combineQuantities(
  input: CombineInput,
): Promise<CombineResult> {
  const { status, data } = await apiRequestWithStatus<CombineResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/merges`,
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        quantity_flow_ids: input.quantityFlowIds,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    movementId: data.movement_id,
    quantityFlowId: data.quantity_flow_id,
    partNumber: data.part_number,
    quantity: data.quantity,
    areaId: data.area_id,
    machineId: data.machine_id,
    operationId: data.operation_id,
    stationId: data.station_id,
    processingState: data.processing_state,
    sourceQuantityFlowIds: data.source_quantity_flow_ids,
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
// Receive Quantity — the MODIFY intake (Phase 10.5)
// ---------------------------------------------------------------------------

export interface ReceiptInput {
  stationId: string;
  partNumber: string;
  /** The physically received quantity — no MAX and no default. */
  quantity: number;
  /** Editable default `MODIFY` (GUI_DESIGN §4.7), never forced. */
  requestType: 'NEW' | 'MODIFY';
  /** Editable default `FLOATING`, never forced. */
  routeMode: 'FLOATING' | 'PLANNED';
  /** Only a PLANNED receipt carries a Planned Route. */
  routeTemplateId: number | null;
  /** The Area's Operation; null when the Area resolves it itself. */
  operationId: number | null;
  /** Owned by the WorkOrderDemand — the PN never owns a due date. */
  dueDate: string | null;
  reason: string | null;
  /** The explicitly selected internal blank-number MODIFY Work Order
   * when several were plausible; null otherwise — the client never
   * picks one itself. */
  workOrderId: number | null;
  /** The `scannedAt` of the resolution this wizard was opened from,
   * unchanged: `received_date` follows the SCAN (PROJECT_PROFILE §14).
   * It is part of the receipt's intent, so a retry of the SAME intent
   * replays instead of receiving under a later date. */
  scannedAt: string;
  /** PROJECT_PROFILE §14: a receipt NEVER joins existing quantity. Set
   * only after the wizard showed the PN's existing active
   * distribution and the operator confirmed that this receipt creates
   * a SEPARATE quantity. It is NOT part of the request fingerprint, so
   * confirming continues the same submission under the same
   * `deviceEventId`. */
  confirmActiveQuantity: boolean;
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface ReceiptResult {
  movementId: number;
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  requestType: 'NEW' | 'MODIFY';
  routeMode: 'FLOATING' | 'PLANNED';
  assignedRouteId: number | null;
  areaId: number;
  operationId: number;
  /** QUEUED (Area with Machines) or PROCESSING (Area without). */
  processingState: ProcessingState;
  workOrderId: number;
  workOrderDemandId: number;
  /** An existing internal Work Order took this receipt (§14 reuse). */
  workOrderReused: boolean;
  reason: string | null;
  stationId: string;
  deviceEventId: string;
  occurredAt: string;
  /** False for an idempotent replay — nothing was recorded twice. */
  created: boolean;
}

interface ReceiptResultWire {
  movement_id: number;
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  request_type: 'NEW' | 'MODIFY';
  route_mode: 'FLOATING' | 'PLANNED';
  assigned_route_id: number | null;
  area_id: number;
  operation_id: number;
  processing_state: ProcessingState;
  work_order_id: number;
  work_order_demand_id: number;
  work_order_reused: boolean;
  reason: string | null;
  station_id: string;
  device_event_id: string;
  occurred_at: string;
}

/**
 * Record the confirmed `Receive Quantity` (GUI_DESIGN §4.7,
 * PROJECT_PROFILE §14). Resolves ONLY when the server confirmed the
 * write: 201 for a fresh receipt, 200 for an idempotent replay of the
 * same `deviceEventId` + same intent (a replay is never refused for
 * age — the scan window governs new quantity only). Every rejection —
 * active demand that appeared meanwhile, active quantity without the
 * explicit separate-quantity confirmation, a stale station / Area /
 * Operation context, a terminal Area, several plausible internal Work
 * Orders, a scan timestamp the server no longer accepts for a fresh
 * receipt — is an `ApiError` and nothing was recorded.
 */
export async function receiveQuantity(
  input: ReceiptInput,
): Promise<ReceiptResult> {
  const { status, data } = await apiRequestWithStatus<ReceiptResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/receipts`,
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        quantity: input.quantity,
        request_type: input.requestType,
        route_mode: input.routeMode,
        route_template_id: input.routeTemplateId,
        operation_id: input.operationId,
        due_date: input.dueDate,
        reason: input.reason,
        work_order_id: input.workOrderId,
        scanned_at: input.scannedAt,
        confirm_active_quantity: input.confirmActiveQuantity,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    movementId: data.movement_id,
    quantityFlowId: data.quantity_flow_id,
    partNumber: data.part_number,
    quantity: data.quantity,
    requestType: data.request_type,
    routeMode: data.route_mode,
    assignedRouteId: data.assigned_route_id,
    areaId: data.area_id,
    operationId: data.operation_id,
    processingState: data.processing_state,
    workOrderId: data.work_order_id,
    workOrderDemandId: data.work_order_demand_id,
    workOrderReused: data.work_order_reused,
    reason: data.reason,
    stationId: data.station_id,
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}

/**
 * The PN's existing ACTIVE distribution a refused receipt asks the
 * operator to confirm against (the backend's 409 with
 * `confirmation_required`), or null for any other failure. It arrives
 * when quantity appeared after the resolution opened the wizard;
 * nothing was recorded, and the SAME `deviceEventId` continues the
 * submission once the operator confirmed.
 */
export function receiptConfirmationRequired(
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
  return (record.existing_active_quantity as ActiveQuantityWire[]).map(
    toActiveQuantity,
  );
}

/**
 * The internal blank-number MODIFY Work Orders a refused receipt asks
 * the operator to choose between (the backend's 409 with
 * `selection_required`), or null for any other failure. Nothing was
 * recorded either way.
 */
export function workOrderSelectionRequired(
  error: unknown,
): InternalWorkOrder[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body;
  if (!body || typeof body !== 'object') return null;
  const record = body as {
    selection_required?: unknown;
    work_orders?: unknown;
  };
  if (record.selection_required !== true) return null;
  if (!Array.isArray(record.work_orders)) return null;
  return (record.work_orders as InternalWorkOrderWire[]).map(
    toInternalWorkOrder,
  );
}

// ---------------------------------------------------------------------------
// Scrap and quantity addition (Phase 9)
// ---------------------------------------------------------------------------

export interface ScrapInput {
  stationId: string;
  partNumber: string;
  /** The ONE flow the damaged quantity is scrapped from. */
  quantityFlowId: number;
  /** The total counted with the scrap barcode; smaller than the flow's
   * it splits the flow first (Phase 8) and the remainder keeps its
   * state and place. */
  quantity: number;
  /** The one common scrap reason — mandatory. */
  reason: string;
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface ScrapResult {
  movementId: number;
  /** The flow the scrap closed (the source, or the selected child of a
   * partial scrap). */
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  areaId: number;
  /** The Machine the scrapped quantity left; null unless ON_MACHINE. */
  machineId: number | null;
  reason: string;
  stationId: string;
  /** Phase 8 — set when only a part of the source was scrapped. */
  sourceQuantityFlowId: number | null;
  remainderQuantityFlowId: number | null;
  remainderQuantity: number | null;
  deviceEventId: string;
  occurredAt: string;
  /** false when the server replayed an already committed scrap. */
  created: boolean;
}

interface ScrapResultWire {
  movement_id: number;
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  area_id: number;
  machine_id: number | null;
  reason: string;
  station_id: string;
  source_quantity_flow_id: number | null;
  remainder_quantity_flow_id: number | null;
  remainder_quantity: number | null;
  device_event_id: string;
  occurred_at: string;
}

/**
 * Record one confirmed Scrap — ONE auditable SCRAPPED operation for the
 * total counted quantity, with its mandatory reason. Resolves ONLY when
 * the server confirmed the write: 201 fresh, 200 for an idempotent
 * replay of the same `deviceEventId` + same intent. Every rejection —
 * a quantity exceeding the flow, a flow that moved or was consumed
 * meanwhile — is an `ApiError` and nothing was recorded.
 */
export async function scrapQuantity(input: ScrapInput): Promise<ScrapResult> {
  const { status, data } = await apiRequestWithStatus<ScrapResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/scraps`,
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        quantity_flow_id: input.quantityFlowId,
        quantity: input.quantity,
        reason: input.reason,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    movementId: data.movement_id,
    quantityFlowId: data.quantity_flow_id,
    partNumber: data.part_number,
    quantity: data.quantity,
    areaId: data.area_id,
    machineId: data.machine_id,
    reason: data.reason,
    stationId: data.station_id,
    sourceQuantityFlowId: data.source_quantity_flow_id,
    remainderQuantityFlowId: data.remainder_quantity_flow_id,
    remainderQuantity: data.remainder_quantity,
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}

export interface QuantityAdditionInput {
  stationId: string;
  partNumber: string;
  /** The entered quantity — no MAX and no default (GUI_DESIGN §4.7). */
  quantity: number;
  /** Mandatory. */
  reason: string;
  /** null when the Area resolves its single active Operation itself;
   * required when several are configured. */
  operationId: number | null;
  /** Client-generated UUID, reused verbatim on every retry of the SAME
   * confirmed intent (idempotency key). */
  deviceEventId: string;
}

export interface QuantityAdditionResult {
  movementId: number;
  /** The NEW Quantity Flow the addition introduced. */
  quantityFlowId: number;
  partNumber: string;
  quantity: number;
  areaId: number;
  operationId: number;
  /** QUEUED (Area with Machines) or PROCESSING (Area without). */
  processingState: ProcessingState;
  reason: string;
  stationId: string;
  deviceEventId: string;
  occurredAt: string;
  /** false when the server replayed an already committed addition. */
  created: boolean;
}

interface QuantityAdditionResultWire {
  movement_id: number;
  quantity_flow_id: number;
  part_number: string;
  quantity: number;
  area_id: number;
  operation_id: number;
  processing_state: ProcessingState;
  reason: string;
  station_id: string;
  device_event_id: string;
  occurred_at: string;
}

/**
 * Record one confirmed quantity addition (`Add more quantity`): found
 * physical quantity enters as a NEW FLOATING Quantity Flow recorded as
 * `QUANTITY_ADJUSTED · INCREASE` with its mandatory reason — never as
 * a transfer, never changing any requested quantity. Resolves ONLY
 * when the server confirmed the write: 201 fresh, 200 for an
 * idempotent replay. A PN with no active quantity in the station's
 * Area is refused (that is the receive workflow, not an addition) and
 * nothing was recorded.
 */
export async function addQuantity(
  input: QuantityAdditionInput,
): Promise<QuantityAdditionResult> {
  const { status, data } =
    await apiRequestWithStatus<QuantityAdditionResultWire>(
      `/api/scan-stations/${encodeURIComponent(input.stationId)}/quantity-additions`,
      {
        method: 'POST',
        body: {
          part_number: input.partNumber,
          quantity: input.quantity,
          reason: input.reason,
          ...(input.operationId === null
            ? {}
            : { operation_id: input.operationId }),
          device_event_id: input.deviceEventId,
        },
      },
    );
  return {
    movementId: data.movement_id,
    quantityFlowId: data.quantity_flow_id,
    partNumber: data.part_number,
    quantity: data.quantity,
    areaId: data.area_id,
    operationId: data.operation_id,
    processingState: data.processing_state,
    reason: data.reason,
    stationId: data.station_id,
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}

// ---------------------------------------------------------------------------
// Undo (Phase 9 — PROJECT_PROFILE §16)
// ---------------------------------------------------------------------------

export type FlowStatus =
  'ACTIVE' | 'SPLIT' | 'MERGED' | 'SCRAPPED' | 'REVERSED';

/** One original Movement of the command an Undo would reverse. */
export interface UndoMovementSummary {
  movementId: number;
  movementType: string;
  /** `REPAIR` on a Repair transfer; null otherwise. */
  movementReason: string | null;
  quantity: number;
  fromArea: AreaRef | null;
  toArea: AreaRef;
  machineId: number | null;
  operationId: number;
}

/** The effect of the reversal on one Quantity Flow, for the summary. */
export interface RestoredFlowPreview {
  quantityFlowId: number;
  quantity: number;
  /** ACTIVE: reopened/repositioned with the restored Area/Machine.
   * REVERSED: the command created this flow, so it closes. */
  status: FlowStatus;
  area: AreaRef | null;
  machineId: number | null;
  processingState: ProcessingState | null;
}

/** The §16 summary confirmation of undoing one complete command — a
 * read: original action, quantity, source/destination, Machine,
 * timestamp, the exact effect of the reversal, and whether Undo is
 * currently eligible (the command re-judges under its locks). */
export interface UndoPreview {
  reversesDeviceEventId: string;
  stationId: string;
  /** The command kind as recorded (TRANSFER, ASSIGN, QUEUE, DONE,
   * MERGE, SCRAP, ADD); null for a row recorded without one. */
  kind: string | null;
  partNumber: string;
  quantity: number;
  occurredAt: string;
  eligible: boolean;
  ineligibleReason: string | null;
  movements: UndoMovementSummary[];
  restored: RestoredFlowPreview[];
}

interface UndoMovementSummaryWire {
  movement_id: number;
  movement_type: string;
  movement_reason: string | null;
  quantity: number;
  from_area: AreaRefWire | null;
  to_area: AreaRefWire;
  machine_id: number | null;
  operation_id: number;
}

interface RestoredFlowPreviewWire {
  quantity_flow_id: number;
  quantity: number;
  status: FlowStatus;
  area: AreaRefWire | null;
  machine_id: number | null;
  processing_state: ProcessingState | null;
}

interface UndoPreviewWire {
  reverses_device_event_id: string;
  station_id: string;
  kind: string | null;
  part_number: string;
  quantity: number;
  occurred_at: string;
  eligible: boolean;
  ineligible_reason: string | null;
  movements: UndoMovementSummaryWire[];
  restored: RestoredFlowPreviewWire[];
}

/**
 * Load the summary confirmation of undoing the command recorded under
 * `deviceEventId` (GUI_DESIGN §4.5). A read — nothing is locked and
 * nothing is recorded; the Undo command itself re-judges eligibility
 * authoritatively under its row locks.
 */
export async function getUndoPreview(
  stationId: string,
  deviceEventId: string,
): Promise<UndoPreview> {
  const wire = await apiRequest<UndoPreviewWire>(
    `/api/scan-stations/${encodeURIComponent(stationId)}/undo-preview/${encodeURIComponent(deviceEventId)}`,
  );
  return {
    reversesDeviceEventId: wire.reverses_device_event_id,
    stationId: wire.station_id,
    kind: wire.kind,
    partNumber: wire.part_number,
    quantity: wire.quantity,
    occurredAt: wire.occurred_at,
    eligible: wire.eligible,
    ineligibleReason: wire.ineligible_reason,
    movements: wire.movements.map((item) => ({
      movementId: item.movement_id,
      movementType: item.movement_type,
      movementReason: item.movement_reason,
      quantity: item.quantity,
      fromArea: item.from_area ? toAreaRef(item.from_area) : null,
      toArea: toAreaRef(item.to_area),
      machineId: item.machine_id,
      operationId: item.operation_id,
    })),
    restored: wire.restored.map((item) => ({
      quantityFlowId: item.quantity_flow_id,
      quantity: item.quantity,
      status: item.status,
      area: item.area ? toAreaRef(item.area) : null,
      machineId: item.machine_id,
      processingState: item.processing_state,
    })),
  };
}

export interface UndoInput {
  stationId: string;
  partNumber: string;
  /** The command to reverse — every Movement recorded under it. */
  reversesDeviceEventId: string;
  /** The Undo's OWN idempotency key (a new production event), reused
   * verbatim on every retry of the same reversal. */
  deviceEventId: string;
}

export interface ReversedMovement {
  movementId: number;
  reversesMovementId: number;
  originalMovementType: string;
}

export interface RestoredFlow {
  quantityFlowId: number;
  quantity: number;
  status: FlowStatus;
  currentAreaId: number | null;
  currentMachineId: number | null;
}

export interface UndoResult {
  reversesDeviceEventId: string;
  /** The reversed command's kind as recorded (TRANSFER, ASSIGN, …). */
  reversedKind: string | null;
  partNumber: string;
  stationId: string;
  movements: ReversedMovement[];
  flows: RestoredFlow[];
  deviceEventId: string;
  occurredAt: string;
  /** false when the server replayed an already committed Undo. */
  created: boolean;
}

interface UndoResultWire {
  reverses_device_event_id: string;
  reversed_kind: string | null;
  part_number: string;
  station_id: string;
  movements: {
    movement_id: number;
    reverses_movement_id: number;
    original_movement_type: string;
  }[];
  flows: {
    quantity_flow_id: number;
    quantity: number;
    status: FlowStatus;
    current_area_id: number | null;
    current_machine_id: number | null;
  }[];
  device_event_id: string;
  occurred_at: string;
}

/**
 * Reverse one complete committed command (the confirmed Undo). The
 * original history is never deleted: the server appends one
 * compensating REVERSED Movement per original row and restores every
 * involved flow. Resolves ONLY when the server confirmed the write:
 * 201 fresh, 200 for an idempotent replay of the same `deviceEventId`.
 * Every rejection — already reversed, no longer the most recent
 * operation, recorded elsewhere, a retired Machine or deactivated Area
 * in the way — is an `ApiError` and nothing was reversed.
 */
export async function undoProductionCommand(
  input: UndoInput,
): Promise<UndoResult> {
  const { status, data } = await apiRequestWithStatus<UndoResultWire>(
    `/api/scan-stations/${encodeURIComponent(input.stationId)}/undos`,
    {
      method: 'POST',
      body: {
        part_number: input.partNumber,
        reverses_device_event_id: input.reversesDeviceEventId,
        device_event_id: input.deviceEventId,
      },
    },
  );
  return {
    reversesDeviceEventId: data.reverses_device_event_id,
    reversedKind: data.reversed_kind,
    partNumber: data.part_number,
    stationId: data.station_id,
    movements: data.movements.map((item) => ({
      movementId: item.movement_id,
      reversesMovementId: item.reverses_movement_id,
      originalMovementType: item.original_movement_type,
    })),
    flows: data.flows.map((item) => ({
      quantityFlowId: item.quantity_flow_id,
      quantity: item.quantity,
      status: item.status,
      currentAreaId: item.current_area_id,
      currentMachineId: item.current_machine_id,
    })),
    deviceEventId: data.device_event_id,
    occurredAt: data.occurred_at,
    created: status === 201,
  };
}
