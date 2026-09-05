// Shared Area monitoring model (Phase 5 → Phase 11 — GUI_DESIGN §4.10,
// §6; PROJECT_PROFILE §12, §21).
//
// ONE client model of the Area/Machine monitoring read, used by every
// view that renders it: the Scan Station (`In this Area now`) and the
// Area Board (the All Areas overview and the per-Area detail). Both
// read it from the same backend contract (`app/api/area_inventory.py`)
// and render it through the same shared components, so the same
// quantity can never be presented differently by the two — the drift
// PROJECT_PROFILE §21 forbids between them.
//
// Wire shapes are the backend's snake_case; the exported types are the
// camelCase the views use. No business rules live here: every state,
// entry timestamp and Machine attribution is the server's derivation.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

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

/**
 * The demand a quantity ORIGINATED from — provenance, not monitoring:
 * the Scan Station's recaps and the audit trail read it, and it stays
 * what it is after that Work Order completed. What the PN is currently
 * being worked FOR is `DemandContext` below.
 */
export interface WorkOrderContext {
  workOrderId: number;
  /** null for an internal blank-number Work Order — rendered `—`. */
  workOrderNumber: string | null;
  workOrderDemandId: number;
  requestType: 'NEW' | 'MODIFY';
}

/**
 * One OPEN Work Order Demand of a PN — the monitoring context the
 * shared PN row shows (§4.10): the Work Order Number, the external Job
 * Numbers, the due date the countdown is derived from (null is valid
 * data), the Hot rank, and the Work Order's received date, which
 * orders undated demand.
 */
export interface DemandContext {
  workOrderId: number;
  workOrderNumber: string | null;
  workOrderDemandId: number;
  requestType: 'NEW' | 'MODIFY';
  requestedQuantity: number;
  jobNumbers: string[];
  dueDate: string | null;
  priorityRank: number | null;
  receivedDate: string;
}

/** Derived holding state of a flow (PROJECT_PROFILE §12), from its
 * latest Movement AND the mode of the Area it is in: QUEUED /
 * ON_MACHINE in an Area with Machines, PROCESSING — owned directly by
 * an Area without Machines, no queue, no Machine (Phase 7) — and
 * READY_TO_TRANSFER on the finished rack. A null Machine is never
 * "queued" by itself. */
export type ProcessingState =
  'QUEUED' | 'PROCESSING' | 'ON_MACHINE' | 'READY_TO_TRANSFER';

/** The actions the server reports as currently valid for a flow.
 * `SCRAP` (Phase 9) is reported in every state — damaged quantity can
 * be scrapped wherever it physically is. */
export type FlowAction = 'ASSIGN' | 'DONE' | 'QUEUE' | 'TRANSFER' | 'SCRAP';

/** The Operation RECORDED on a flow's latest Movement, as recorded —
 * whatever its current activation. Existing quantity keeps it even
 * after the Operation was deactivated; it is independent of the active
 * Operations a station offers for new arrivals. */
export interface RecordedOperation {
  id: number;
  code: string;
  name: string | null;
  isExternal: boolean;
  isActive: boolean;
}

export interface FlowInArea {
  partNumber: string;
  quantityFlowId: number;
  quantity: number;
  routeMode: 'FLOATING' | 'PLANNED';
  /** The Operation the quantity is in the Area for (recorded, not
   * looked up among the station's active Operations). */
  operation: RecordedOperation;
  processingState: ProcessingState;
  /** Set exactly while ON_MACHINE. */
  machineId: number | null;
  /**
   * The Machine that COMPLETED finished quantity (READY_TO_TRANSFER) —
   * completion context only: the quantity is no longer assigned to it
   * and stays in the Area until transferred. null in every other state
   * and on merged quantity whose branches disagree. The Machine
   * itself, so a Machine retired after finishing the work — no longer
   * an Area card — still names the completion.
   */
  completedMachine: { id: number; name: string } | null;
  /**
   * ISO timestamp of the moment this quantity entered its current
   * position. `Time in Area` and its sort order are DERIVED from it at
   * render (the shared UI clock, §3.12) — never a stored duration.
   */
  enteredAt: string;
  availableActions: FlowAction[];
  workOrder: WorkOrderContext | null;
}

export type MachineOperationalState = 'MAINTENANCE' | 'RUNNING' | 'IDLE';

/** A Machine as the station read models present it, with its DERIVED
 * operational state and the moment that state last changed. */
export interface MachineRef {
  id: number;
  name: string;
  assetTag: string;
  barcodeValue: string;
  operationalState: MachineOperationalState;
  stateChangedAt: string;
  maintenanceSince: string | null;
  maintenanceNote: string | null;
  maintenanceExpectedReturn: string | null;
}

export interface AreaRefWire {
  id: number;
  name: string;
  color: string | null;
  description: string | null;
  is_terminal: boolean;
}

export interface OperationRefWire {
  id: number;
  code: string;
  name: string | null;
  is_external: boolean;
}

export interface WorkOrderContextWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_demand_id: number;
  request_type: 'NEW' | 'MODIFY';
}

interface DemandContextWire {
  work_order_id: number;
  work_order_number: string | null;
  work_order_demand_id: number;
  request_type: 'NEW' | 'MODIFY';
  requested_quantity: number;
  job_numbers: string[];
  due_date: string | null;
  priority_rank: number | null;
  received_date: string;
}

interface PartNumberDemandsWire {
  part_number: string;
  demands: DemandContextWire[];
}

export interface FlowInAreaWire {
  part_number: string;
  quantity_flow_id: number;
  quantity: number;
  route_mode: 'FLOATING' | 'PLANNED';
  operation: {
    id: number;
    code: string;
    name: string | null;
    is_external: boolean;
    is_active: boolean;
  };
  processing_state: ProcessingState;
  machine_id: number | null;
  completed_machine: { id: number; name: string } | null;
  entered_at: string;
  available_actions: FlowAction[];
  work_order: WorkOrderContextWire | null;
}

export interface MachineRefWire {
  id: number;
  name: string;
  asset_tag: string;
  barcode_value: string;
  operational_state: MachineOperationalState;
  state_changed_at: string;
  maintenance_since: string | null;
  maintenance_note: string | null;
  maintenance_expected_return: string | null;
}

export function toMachineRef(wire: MachineRefWire): MachineRef {
  return {
    id: wire.id,
    name: wire.name,
    assetTag: wire.asset_tag,
    barcodeValue: wire.barcode_value,
    operationalState: wire.operational_state,
    stateChangedAt: wire.state_changed_at,
    maintenanceSince: wire.maintenance_since,
    maintenanceNote: wire.maintenance_note,
    maintenanceExpectedReturn: wire.maintenance_expected_return,
  };
}

export function toAreaRef(wire: AreaRefWire): AreaRef {
  return {
    id: wire.id,
    name: wire.name,
    color: wire.color,
    description: wire.description,
    isTerminal: wire.is_terminal,
  };
}

export function toOperationRef(wire: OperationRefWire): OperationRef {
  return {
    id: wire.id,
    code: wire.code,
    name: wire.name,
    isExternal: wire.is_external,
  };
}

export function toWorkOrderContext(
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

function toDemandContext(wire: DemandContextWire): DemandContext {
  return {
    workOrderId: wire.work_order_id,
    workOrderNumber: wire.work_order_number,
    workOrderDemandId: wire.work_order_demand_id,
    requestType: wire.request_type,
    requestedQuantity: wire.requested_quantity,
    jobNumbers: [...wire.job_numbers],
    dueDate: wire.due_date,
    priorityRank: wire.priority_rank,
    receivedDate: wire.received_date,
  };
}

export function toFlowInArea(wire: FlowInAreaWire): FlowInArea {
  return {
    partNumber: wire.part_number,
    quantityFlowId: wire.quantity_flow_id,
    quantity: wire.quantity,
    routeMode: wire.route_mode,
    operation: {
      id: wire.operation.id,
      code: wire.operation.code,
      name: wire.operation.name,
      isExternal: wire.operation.is_external,
      isActive: wire.operation.is_active,
    },
    processingState: wire.processing_state,
    machineId: wire.machine_id,
    completedMachine: wire.completed_machine,
    enteredAt: wire.entered_at,
    availableActions: [...wire.available_actions],
    workOrder: toWorkOrderContext(wire.work_order),
  };
}

/** CSS color of an Area reference (fallback for Areas without one). */
export function areaRefColor(area: Pick<AreaRef, 'color'> | null): string {
  return area?.color ?? 'var(--faint)';
}

// ---------------------------------------------------------------------------
// Area inventory
// ---------------------------------------------------------------------------

export interface InventoryLine {
  partNumber: string;
  totalQuantity: number;
  flows: FlowInArea[];
}

/** One Machine card: the Machine and ONLY its ON_MACHINE quantity. */
export interface MachineInventory {
  machine: MachineRef;
  lines: InventoryLine[];
  totalQuantity: number;
}

export interface AreaInventory {
  area: AreaRef;
  /**
   * What each PN in the Area is currently being worked FOR: its OPEN
   * demands in the canonical order, the FIRST one defining the row's
   * Hot rank and due date. Every other stays listed — never summed
   * away — and a PN whose every Work Order is complete is simply
   * absent: its quantity is still shown, without a context it no
   * longer has.
   */
  demandContext: Record<string, DemandContext[]>;
  /** The Area mode (PROJECT_PROFILE §12), decided by the server from
   * the Area's active Machines: true → queued / Machine cards /
   * finished; false → directly processing / finished with no cards. */
  hasMachines: boolean;
  /** Every ACTIVE flow per PN whatever its state. */
  lines: InventoryLine[];
  totalPartNumbers: number;
  totalQuantity: number;
  /** The same flows split by derived state (Phase 6): queued and
   * finished are Area summary figures; on-Machine quantity sits on the
   * Machine cards — every active Machine, with or without quantity. */
  queued: InventoryLine[];
  queuedQuantity: number;
  machines: MachineInventory[];
  onMachineQuantity: number;
  /** Phase 7: directly processing quantity of an Area without Machines. */
  processing: InventoryLine[];
  processingQuantity: number;
  finished: InventoryLine[];
  finishedQuantity: number;
}

export interface InventoryLineWire {
  part_number: string;
  total_quantity: number;
  flows: FlowInAreaWire[];
}

export interface AreaInventoryWire {
  area: AreaRefWire;
  demand_context: PartNumberDemandsWire[];
  has_machines: boolean;
  lines: InventoryLineWire[];
  total_part_numbers: number;
  total_quantity: number;
  queued: InventoryLineWire[];
  queued_quantity: number;
  machines: {
    machine: MachineRefWire;
    lines: InventoryLineWire[];
    total_quantity: number;
  }[];
  on_machine_quantity: number;
  processing: InventoryLineWire[];
  processing_quantity: number;
  finished: InventoryLineWire[];
  finished_quantity: number;
}

export function toInventoryLines(lines: InventoryLineWire[]): InventoryLine[] {
  return lines.map((line) => ({
    partNumber: line.part_number,
    totalQuantity: line.total_quantity,
    flows: line.flows.map(toFlowInArea),
  }));
}

/** The ONE converter of the shared Area monitoring answer. */
export function toAreaInventory(wire: AreaInventoryWire): AreaInventory {
  return {
    area: toAreaRef(wire.area),
    demandContext: Object.fromEntries(
      wire.demand_context.map((entry) => [
        entry.part_number,
        entry.demands.map(toDemandContext),
      ]),
    ),
    hasMachines: wire.has_machines,
    lines: toInventoryLines(wire.lines),
    totalPartNumbers: wire.total_part_numbers,
    totalQuantity: wire.total_quantity,
    queued: toInventoryLines(wire.queued),
    queuedQuantity: wire.queued_quantity,
    machines: wire.machines.map((card) => ({
      machine: toMachineRef(card.machine),
      lines: toInventoryLines(card.lines),
      totalQuantity: card.total_quantity,
    })),
    onMachineQuantity: wire.on_machine_quantity,
    processing: toInventoryLines(wire.processing),
    processingQuantity: wire.processing_quantity,
    finished: toInventoryLines(wire.finished),
    finishedQuantity: wire.finished_quantity,
  };
}

export async function getAreaInventory(areaId: number): Promise<AreaInventory> {
  const wire = await apiRequest<AreaInventoryWire>(
    `/api/areas/${areaId}/inventory`,
  );
  return toAreaInventory(wire);
}
