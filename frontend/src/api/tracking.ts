// PN Tracking read model API (Phase 11 — GUI_DESIGN §7;
// PROJECT_PROFILE §21 Tracking).
//
// Two reads: `GET /api/tracking` returns the filtered PN list in the
// canonical demand order, one bounded page (offset / limit, the
// matching total); `GET /api/tracking/detail?part_number=` returns one
// PN's read-only detail — the optional master and derived barcode, the
// open demand with released / allocated / shortage figures, the
// current quantity by Area / Machine (the shared monitoring
// derivation), the stocked quantity per Area with the active
// allocation, the reconciliation figures, the Quantity Flows with their
// lineage, routes and actual traces, the allocation history and the
// first page of the immutable Movement history, which
// `GET /api/tracking/movements` continues below a Movement — in the
// reverse-chronological `(occurred_at DESC, id DESC)` order the server
// resolves from that Movement's timestamp. The Scrap history is the
// same history restricted to `SCRAPPED` rows; the closed Quantity
// Flows and the allocation history page the same way through
// `GET /api/tracking/flows` and `GET /api/tracking/allocations`.
//
// Search and every filter are judged server-side on the derived row;
// the server never sends a derived time value — dwell times and due
// countdowns derive at render from the fixed timestamps and the shared
// UI clock (§3.12).
//
// Wire responses are the backend's snake_case schemas; this module maps
// them to the camelCase model the view renders.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TrackingStatus = 'ACTIVE' | 'STOCKED' | 'OPEN' | 'COMPLETED';
export type TrackingStatusFilter = TrackingStatus | 'ALL';
export type TrackingDueWindow = 'ANY' | 'OVERDUE' | 'THIS_WEEK' | 'THIS_MONTH';
export type LocationState =
  'MACHINE' | 'QUEUE' | 'PROCESSING' | 'DONE' | 'STOCKED';
export type RouteStepState = 'DONE' | 'CURRENT' | 'FUTURE';

export interface TrackingFilters {
  search: string;
  areaId: number | null;
  operationId: number | null;
  machineId: number | null;
  requestType: 'NEW' | 'MODIFY' | null;
  hotOnly: boolean;
  status: TrackingStatusFilter;
  due: TrackingDueWindow;
}

export const DEFAULT_TRACKING_FILTERS: TrackingFilters = {
  search: '',
  areaId: null,
  operationId: null,
  machineId: null,
  requestType: null,
  hotOnly: false,
  status: 'ACTIVE',
  due: 'ANY',
};

export interface TrackingAreaRef {
  id: number;
  name: string;
  color: string | null;
  isTerminal: boolean;
}

export interface TrackingMachineRef {
  id: number;
  name: string;
}

export interface TrackingOperationRef {
  id: number;
  code: string;
  name: string | null;
  isExternal: boolean;
}

/** One OPEN Work Order Demand of the PN (the monitoring context). */
export interface TrackingDemand {
  workOrderId: number;
  /** Verbatim external number, or null on an internal Work Order —
   * rendered as `—` (display-only). */
  workOrderNumber: string | null;
  workOrderDemandId: number;
  requestType: 'NEW' | 'MODIFY';
  requestedQuantity: number;
  allocatedQuantity: number;
  jobNumbers: string[];
  /** ISO `YYYY-MM-DD`, or null — valid data, not missing data. */
  dueDate: string | null;
  priorityRank: number | null;
}

export interface TrackingDetailDemand extends TrackingDemand {
  releasedQuantity: number;
  /** `requested − allocated` (never negative). */
  shortage: number;
}

export interface TrackingDistribution {
  area: TrackingAreaRef;
  quantity: number;
  /** Stocked (manufacturing-complete) quantity in a terminal Area, as
   * opposed to active quantity in production. */
  stocked: boolean;
}

export interface TrackingRow {
  pn: string;
  /** The optional PartNumber master exists; the canonical PN and its
   * history render normally either way (PROJECT_PROFILE §8.1). */
  hasMaster: boolean;
  barcodeValue: string;
  hotRank: number | null;
  demands: TrackingDemand[];
  distribution: TrackingDistribution[];
  activeQuantity: number;
  stockedQuantity: number;
  /** The PN's active allocation and the stocked quantity it leaves
   * unallocated — the figure the STOCKED / OPEN status is judged on. */
  allocatedQuantity: number;
  availableStockedQuantity: number;
  scrappedQuantity: number;
  /** The earliest due date among the open demands; null when none is
   * dated. */
  nextDueDate: string | null;
  status: TrackingStatus;
}

export interface TrackingPage {
  rows: TrackingRow[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
}

/** One distributed quantity position (the board's own location shape). */
export interface TrackingLocation {
  area: TrackingAreaRef;
  /** Executor for MACHINE quantity; completion context for DONE. */
  machine: TrackingMachineRef | null;
  activity: string | null;
  quantity: number;
  state: LocationState;
  /** ISO timestamp the oldest portion entered the position; null where
   * elapsed time does not apply. */
  since: string | null;
}

export interface TrackingFlowPosition {
  area: TrackingAreaRef;
  machine: TrackingMachineRef | null;
  operation: TrackingOperationRef;
  activity: string | null;
  state: LocationState;
  since: string;
}

export interface TrackingTraceStep {
  movementId: number;
  /** The flow the arrival was written on — an ancestor's for an
   * inherited step. */
  quantityFlowId: number;
  movementType: string;
  area: TrackingAreaRef;
  occurredAt: string;
  repair: boolean;
  inherited: boolean;
}

export interface TrackingRouteStep {
  id: number;
  sequence: number;
  area: TrackingAreaRef;
  operation: TrackingOperationRef | null;
  state: RouteStepState;
}

export interface TrackingRouteDeviation {
  movementId: number;
  occurredAt: string;
  kind: string;
  expectedArea: TrackingAreaRef | null;
  expectedOperation: TrackingOperationRef | null;
  actualArea: TrackingAreaRef;
  actualOperation: TrackingOperationRef | null;
  reason: string | null;
  stationId: string | null;
}

export interface TrackingLineageLink {
  quantityFlowId: number;
  relation: string;
}

export interface TrackingFlow {
  id: number;
  quantity: number;
  status: string;
  routeMode: 'FLOATING' | 'PLANNED';
  createdAt: string;
  closedAt: string | null;
  /** The derived current position — ACTIVE flows only. */
  position: TrackingFlowPosition | null;
  parents: TrackingLineageLink[];
  children: TrackingLineageLink[];
  /** The actual route trace derived from Movement history. */
  trace: TrackingTraceStep[];
  /** PLANNED only: the immutable AssignedRoute snapshot. */
  routeSteps: TrackingRouteStep[];
  sourceTemplate: { id: number; name: string } | null;
  offRoute: boolean;
  deviations: TrackingRouteDeviation[];
}

export interface TrackingWorkOrderRef {
  workOrderId: number;
  workOrderNumber: string | null;
  workOrderDemandId: number;
  requestType: 'NEW' | 'MODIFY';
}

export interface TrackingAllocation {
  id: number;
  quantity: number;
  workOrder: TrackingWorkOrderRef;
  source: string;
  isManualOverride: boolean;
  allocationReason: string | null;
  reversesAllocationId: number | null;
  reversedByAllocationId: number | null;
  stationId: string | null;
  allocatedAt: string;
}

export interface TrackingMovement {
  id: number;
  quantityFlowId: number;
  movementType: string;
  quantity: number;
  fromArea: TrackingAreaRef | null;
  toArea: TrackingAreaRef;
  operation: TrackingOperationRef;
  sourceMachine: TrackingMachineRef | null;
  destinationMachine: TrackingMachineRef | null;
  stationId: string | null;
  occurredAt: string;
  deviceEventId: string;
  commandSequence: number;
  movementReason: string | null;
  reason: string | null;
  reversesMovementId: number | null;
  reversedByMovementId: number | null;
  assignedRouteStep: { id: number; sequence: number } | null;
  routeDeviation: Record<string, unknown> | null;
  lineage: { parentFlowId: number; childFlowId: number; relation: string }[];
  demand: TrackingWorkOrderRef | null;
}

export interface TrackingMovementPage {
  movements: TrackingMovement[];
  total: number;
  hasMore: boolean;
  /** Pass as `before` for the next (older) page; null on the last. */
  nextBeforeMovementId: number | null;
}

/** One bounded page of the PN's Quantity Flows in the one flow order:
 * ACTIVE flows (oldest first) before closed flows (newest first). */
export interface TrackingFlowPage {
  flows: TrackingFlow[];
  total: number;
  hasMore: boolean;
  /** Pass as `before` for the next page (the last flow delivered); null
   * on the last. */
  nextBeforeFlowId: number | null;
}

export interface TrackingAllocationPage {
  allocations: TrackingAllocation[];
  total: number;
  hasMore: boolean;
  nextBeforeAllocationId: number | null;
}

export interface TrackingDetail {
  pn: string;
  /** The optional master record (existence only until Phase 13). */
  master: { partNumber: string; createdAt: string } | null;
  barcodeValue: string;
  status: TrackingStatus;
  demands: TrackingDetailDemand[];
  locations: TrackingLocation[];
  stocked: TrackingDistribution[];
  activeQuantity: number;
  stockedQuantity: number;
  allocatedQuantity: number;
  availableStockedQuantity: number;
  scrappedQuantity: number;
  introducedQuantity: number;
  flows: TrackingFlowPage;
  allocations: TrackingAllocationPage;
  movements: TrackingMovementPage;
  /** The PN's SCRAPPED events — the same immutable history restricted
   * to scrap, newest first; `scrappedQuantity` is the net total. */
  scrapHistory: TrackingMovementPage;
}

// ---------------------------------------------------------------------------
// Wire shapes
// ---------------------------------------------------------------------------

interface AreaRefWire {
  id: number;
  name: string;
  color: string | null;
  is_terminal: boolean;
}

interface OperationRefWire {
  id: number;
  code: string;
  name: string | null;
  is_external: boolean;
}

interface DemandWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_demand_id: number;
  request_type: 'NEW' | 'MODIFY';
  requested_quantity: number;
  allocated_quantity: number;
  job_numbers: string[];
  due_date: string | null;
  priority_rank: number | null;
}

interface DetailDemandWire extends DemandWire {
  released_quantity: number;
  shortage: number;
}

interface DistributionWire {
  area: AreaRefWire;
  quantity: number;
  stocked: boolean;
}

interface RowWire {
  part_number: string;
  has_master: boolean;
  barcode_value: string;
  hot_rank: number | null;
  demands: DemandWire[];
  distribution: DistributionWire[];
  active_quantity: number;
  stocked_quantity: number;
  allocated_quantity: number;
  available_stocked_quantity: number;
  scrapped_quantity: number;
  next_due_date: string | null;
  status: TrackingStatus;
}

interface PageWire {
  rows: RowWire[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

interface WorkOrderRefWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_demand_id: number;
  request_type: 'NEW' | 'MODIFY';
}

interface MovementWire {
  id: number;
  quantity_flow_id: number;
  movement_type: string;
  quantity: number;
  from_area: AreaRefWire | null;
  to_area: AreaRefWire;
  operation: OperationRefWire;
  source_machine: TrackingMachineRef | null;
  destination_machine: TrackingMachineRef | null;
  station_id: string | null;
  occurred_at: string;
  device_event_id: string;
  command_sequence: number;
  movement_reason: string | null;
  reason: string | null;
  reverses_movement_id: number | null;
  reversed_by_movement_id: number | null;
  assigned_route_step: { id: number; sequence: number } | null;
  route_deviation: Record<string, unknown> | null;
  lineage: {
    parent_flow_id: number;
    child_flow_id: number;
    relation: string;
  }[];
  demand: WorkOrderRefWire | null;
}

interface MovementPageWire {
  movements: MovementWire[];
  total: number;
  has_more: boolean;
  next_before_movement_id: number | null;
}

interface FlowWire {
  id: number;
  quantity: number;
  status: string;
  route_mode: 'FLOATING' | 'PLANNED';
  created_at: string;
  closed_at: string | null;
  position: {
    area: AreaRefWire;
    machine: TrackingMachineRef | null;
    operation: OperationRefWire;
    activity: string | null;
    state: LocationState;
    since: string;
  } | null;
  parents: { quantity_flow_id: number; relation: string }[];
  children: { quantity_flow_id: number; relation: string }[];
  trace: {
    movement_id: number;
    quantity_flow_id: number;
    movement_type: string;
    area: AreaRefWire;
    occurred_at: string;
    repair: boolean;
    inherited: boolean;
  }[];
  route_steps: {
    id: number;
    sequence: number;
    area: AreaRefWire;
    operation: OperationRefWire | null;
    expected_duration: string | null;
    state: RouteStepState;
  }[];
  source_template: { id: number; name: string } | null;
  off_route: boolean;
  deviations: {
    movement_id: number;
    occurred_at: string;
    kind: string;
    expected_area: AreaRefWire | null;
    expected_operation: OperationRefWire | null;
    actual_area: AreaRefWire;
    actual_operation: OperationRefWire | null;
    reason: string | null;
    station_id: string | null;
  }[];
}

interface AllocationWire {
  id: number;
  quantity: number;
  work_order: WorkOrderRefWire;
  source: string;
  is_manual_override: boolean;
  allocation_reason: string | null;
  reverses_allocation_id: number | null;
  reversed_by_allocation_id: number | null;
  station_id: string | null;
  allocated_at: string;
}

interface FlowPageWire {
  flows: FlowWire[];
  total: number;
  has_more: boolean;
  next_before_flow_id: number | null;
}

interface AllocationPageWire {
  allocations: AllocationWire[];
  total: number;
  has_more: boolean;
  next_before_allocation_id: number | null;
}

interface DetailWire {
  part_number: string;
  master: { part_number: string; created_at: string } | null;
  barcode_value: string;
  status: TrackingStatus;
  demands: DetailDemandWire[];
  locations: {
    area: AreaRefWire;
    machine: TrackingMachineRef | null;
    activity: string | null;
    quantity: number;
    state: LocationState;
    since: string | null;
  }[];
  stocked: DistributionWire[];
  active_quantity: number;
  stocked_quantity: number;
  allocated_quantity: number;
  available_stocked_quantity: number;
  scrapped_quantity: number;
  introduced_quantity: number;
  flows: FlowPageWire;
  allocations: AllocationPageWire;
  movements: MovementPageWire;
  scrap_history: MovementPageWire;
}

// ---------------------------------------------------------------------------
// Mapping
// ---------------------------------------------------------------------------

function toArea(wire: AreaRefWire): TrackingAreaRef {
  return {
    id: wire.id,
    name: wire.name,
    color: wire.color,
    isTerminal: wire.is_terminal,
  };
}

function toOperation(wire: OperationRefWire): TrackingOperationRef {
  return {
    id: wire.id,
    code: wire.code,
    name: wire.name,
    isExternal: wire.is_external,
  };
}

function toDemand(wire: DemandWire): TrackingDemand {
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

function toDistribution(wire: DistributionWire): TrackingDistribution {
  return {
    area: toArea(wire.area),
    quantity: wire.quantity,
    stocked: wire.stocked,
  };
}

function toRow(wire: RowWire): TrackingRow {
  return {
    pn: wire.part_number,
    hasMaster: wire.has_master,
    barcodeValue: wire.barcode_value,
    hotRank: wire.hot_rank,
    demands: wire.demands.map(toDemand),
    distribution: wire.distribution.map(toDistribution),
    activeQuantity: wire.active_quantity,
    stockedQuantity: wire.stocked_quantity,
    allocatedQuantity: wire.allocated_quantity,
    availableStockedQuantity: wire.available_stocked_quantity,
    scrappedQuantity: wire.scrapped_quantity,
    nextDueDate: wire.next_due_date,
    status: wire.status,
  };
}

function toWorkOrderRef(wire: WorkOrderRefWire): TrackingWorkOrderRef {
  return {
    workOrderId: wire.work_order_id,
    workOrderNumber: wire.work_order_number,
    workOrderDemandId: wire.work_order_demand_id,
    requestType: wire.request_type,
  };
}

function toMovement(wire: MovementWire): TrackingMovement {
  return {
    id: wire.id,
    quantityFlowId: wire.quantity_flow_id,
    movementType: wire.movement_type,
    quantity: wire.quantity,
    fromArea: wire.from_area ? toArea(wire.from_area) : null,
    toArea: toArea(wire.to_area),
    operation: toOperation(wire.operation),
    sourceMachine: wire.source_machine,
    destinationMachine: wire.destination_machine,
    stationId: wire.station_id,
    occurredAt: wire.occurred_at,
    deviceEventId: wire.device_event_id,
    commandSequence: wire.command_sequence,
    movementReason: wire.movement_reason,
    reason: wire.reason,
    reversesMovementId: wire.reverses_movement_id,
    reversedByMovementId: wire.reversed_by_movement_id,
    assignedRouteStep: wire.assigned_route_step,
    routeDeviation: wire.route_deviation,
    lineage: wire.lineage.map((edge) => ({
      parentFlowId: edge.parent_flow_id,
      childFlowId: edge.child_flow_id,
      relation: edge.relation,
    })),
    demand: wire.demand ? toWorkOrderRef(wire.demand) : null,
  };
}

function toMovementPage(wire: MovementPageWire): TrackingMovementPage {
  return {
    movements: wire.movements.map(toMovement),
    total: wire.total,
    hasMore: wire.has_more,
    nextBeforeMovementId: wire.next_before_movement_id,
  };
}

function toFlow(wire: FlowWire): TrackingFlow {
  return {
    id: wire.id,
    quantity: wire.quantity,
    status: wire.status,
    routeMode: wire.route_mode,
    createdAt: wire.created_at,
    closedAt: wire.closed_at,
    position: wire.position
      ? {
          area: toArea(wire.position.area),
          machine: wire.position.machine,
          operation: toOperation(wire.position.operation),
          activity: wire.position.activity,
          state: wire.position.state,
          since: wire.position.since,
        }
      : null,
    parents: wire.parents.map((link) => ({
      quantityFlowId: link.quantity_flow_id,
      relation: link.relation,
    })),
    children: wire.children.map((link) => ({
      quantityFlowId: link.quantity_flow_id,
      relation: link.relation,
    })),
    trace: wire.trace.map((step) => ({
      movementId: step.movement_id,
      quantityFlowId: step.quantity_flow_id,
      movementType: step.movement_type,
      area: toArea(step.area),
      occurredAt: step.occurred_at,
      repair: step.repair,
      inherited: step.inherited,
    })),
    routeSteps: wire.route_steps.map((step) => ({
      id: step.id,
      sequence: step.sequence,
      area: toArea(step.area),
      operation: step.operation ? toOperation(step.operation) : null,
      state: step.state,
    })),
    sourceTemplate: wire.source_template,
    offRoute: wire.off_route,
    deviations: wire.deviations.map((item) => ({
      movementId: item.movement_id,
      occurredAt: item.occurred_at,
      kind: item.kind,
      expectedArea: item.expected_area ? toArea(item.expected_area) : null,
      expectedOperation: item.expected_operation
        ? toOperation(item.expected_operation)
        : null,
      actualArea: toArea(item.actual_area),
      actualOperation: item.actual_operation
        ? toOperation(item.actual_operation)
        : null,
      reason: item.reason,
      stationId: item.station_id,
    })),
  };
}

function toAllocation(wire: AllocationWire): TrackingAllocation {
  return {
    id: wire.id,
    quantity: wire.quantity,
    workOrder: toWorkOrderRef(wire.work_order),
    source: wire.source,
    isManualOverride: wire.is_manual_override,
    allocationReason: wire.allocation_reason,
    reversesAllocationId: wire.reverses_allocation_id,
    reversedByAllocationId: wire.reversed_by_allocation_id,
    stationId: wire.station_id,
    allocatedAt: wire.allocated_at,
  };
}

function toFlowPage(wire: FlowPageWire): TrackingFlowPage {
  return {
    flows: wire.flows.map(toFlow),
    total: wire.total,
    hasMore: wire.has_more,
    nextBeforeFlowId: wire.next_before_flow_id,
  };
}

function toAllocationPage(wire: AllocationPageWire): TrackingAllocationPage {
  return {
    allocations: wire.allocations.map(toAllocation),
    total: wire.total,
    hasMore: wire.has_more,
    nextBeforeAllocationId: wire.next_before_allocation_id,
  };
}

function toDetail(wire: DetailWire): TrackingDetail {
  return {
    pn: wire.part_number,
    master: wire.master
      ? {
          partNumber: wire.master.part_number,
          createdAt: wire.master.created_at,
        }
      : null,
    barcodeValue: wire.barcode_value,
    status: wire.status,
    demands: wire.demands.map((demand) => ({
      ...toDemand(demand),
      releasedQuantity: demand.released_quantity,
      shortage: demand.shortage,
    })),
    locations: wire.locations.map((location) => ({
      area: toArea(location.area),
      machine: location.machine,
      activity: location.activity,
      quantity: location.quantity,
      state: location.state,
      since: location.since,
    })),
    stocked: wire.stocked.map(toDistribution),
    activeQuantity: wire.active_quantity,
    stockedQuantity: wire.stocked_quantity,
    allocatedQuantity: wire.allocated_quantity,
    availableStockedQuantity: wire.available_stocked_quantity,
    scrappedQuantity: wire.scrapped_quantity,
    introducedQuantity: wire.introduced_quantity,
    flows: toFlowPage(wire.flows),
    allocations: toAllocationPage(wire.allocations),
    movements: toMovementPage(wire.movements),
    scrapHistory: toMovementPage(wire.scrap_history),
  };
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

/** The query string of one list read — filters at their non-default
 * values only, so the request stays readable and cacheable. */
export function trackingListQuery(
  filters: TrackingFilters,
  offset: number,
  limit: number,
): string {
  const params = new URLSearchParams();
  const search = filters.search.trim();
  if (search) params.set('search', search);
  if (filters.areaId !== null) params.set('area_id', String(filters.areaId));
  if (filters.operationId !== null) {
    params.set('operation_id', String(filters.operationId));
  }
  if (filters.machineId !== null) {
    params.set('machine_id', String(filters.machineId));
  }
  if (filters.requestType !== null) {
    params.set('request_type', filters.requestType);
  }
  if (filters.hotOnly) params.set('hot_only', 'true');
  if (filters.status !== 'ACTIVE') params.set('status', filters.status);
  if (filters.due !== 'ANY') params.set('due', filters.due);
  if (offset > 0) params.set('offset', String(offset));
  params.set('limit', String(limit));
  return `?${params.toString()}`;
}

export async function loadTrackingList(
  filters: TrackingFilters,
  offset: number,
  limit: number,
): Promise<TrackingPage> {
  return loadTrackingListByQuery(trackingListQuery(filters, offset, limit));
}

/** Load one list page by its ready-made query string (`trackingListQuery`). */
export async function loadTrackingListByQuery(
  query: string,
): Promise<TrackingPage> {
  const wire = await apiRequest<PageWire>(`/api/tracking${query}`);
  return {
    rows: wire.rows.map(toRow),
    total: wire.total,
    offset: wire.offset,
    limit: wire.limit,
    hasMore: wire.has_more,
  };
}

export async function loadTrackingDetail(
  pn: string,
  movementsLimit: number,
): Promise<TrackingDetail> {
  const params = new URLSearchParams({
    part_number: pn,
    movements_limit: String(movementsLimit),
  });
  const wire = await apiRequest<DetailWire>(
    `/api/tracking/detail?${params.toString()}`,
  );
  return toDetail(wire);
}

/**
 * The next (older) page of the Movement history below `before` — the
 * last Movement a page delivered; `movementType: 'SCRAPPED'` continues
 * the Scrap history instead.
 */
export async function loadTrackingMovements(
  pn: string,
  before: number,
  limit: number,
  movementType?: 'SCRAPPED',
): Promise<TrackingMovementPage> {
  const params = new URLSearchParams({
    part_number: pn,
    before: String(before),
    limit: String(limit),
  });
  if (movementType) params.set('movement_type', movementType);
  const wire = await apiRequest<MovementPageWire>(
    `/api/tracking/movements?${params.toString()}`,
  );
  return toMovementPage(wire);
}

/** The next page of Quantity Flows below `before` — the last flow a
 * page delivered; the server resolves its place in the one flow order
 * (the younger ACTIVE flows, then the closed ones). */
export async function loadTrackingFlows(
  pn: string,
  before: number,
  limit: number,
): Promise<TrackingFlowPage> {
  const params = new URLSearchParams({
    part_number: pn,
    before: String(before),
    limit: String(limit),
  });
  const wire = await apiRequest<FlowPageWire>(
    `/api/tracking/flows?${params.toString()}`,
  );
  return toFlowPage(wire);
}

/** The next (older) page of the allocation history below `before`. */
export async function loadTrackingAllocations(
  pn: string,
  before: number,
  limit: number,
): Promise<TrackingAllocationPage> {
  const params = new URLSearchParams({
    part_number: pn,
    before: String(before),
    limit: String(limit),
  });
  const wire = await apiRequest<AllocationPageWire>(
    `/api/tracking/allocations?${params.toString()}`,
  );
  return toAllocationPage(wire);
}
