// PN audit trail API (Phase 14 slice 7 — PROJECT_PROFILE §21 Tracking
// "correction history", §28; GUI_DESIGN §7.4).
//
// One read-only, PN-scoped reader: `GET /api/tracking/audit-trail`
// returns one page of the PN's recorded changes — its Part Number
// details, the Work Orders and Work Order Demand lines that request it
// (priority changes and Work Order completions included), its
// Management allocation entries and every allocation reversal, and the
// route adjustments of its Quantity Flows — newest first. The cursor
// names the last entry a page delivered (`before_source` + `before_id`);
// the server resolves its position, so pages never repeat an entry.
//
// Wire responses are the backend's snake_case schemas; this module maps
// them to the camelCase model the dialog renders — the wire field names
// of a recorded change live only here. Converters throw on a malformed
// answer (an unknown source, kind or field name, a malformed actor)
// instead of rendering a wrong entry.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';
import { toAllocationUserRef } from './management-allocations';
import { toArea, toOperation } from './tracking';
import type {
  TrackingAreaRef,
  TrackingMachineRef,
  TrackingOperationRef,
  TrackingUserRef,
} from './tracking';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AuditTrailSource = 'AUDIT' | 'ALLOCATION';

export type AuditTrailKind =
  | 'PART_NUMBER_CREATED'
  | 'PART_NUMBER_UPDATED'
  | 'PART_NUMBER_IMAGE_CHANGED'
  | 'PART_NUMBER_DELETED'
  | 'WORK_ORDER_CREATED'
  | 'WORK_ORDER_UPDATED'
  | 'WORK_ORDER_COMPLETED'
  | 'DEMAND_CREATED'
  | 'DEMAND_UPDATED'
  | 'PRIORITY_CHANGED'
  | 'ROUTE_ADJUSTED'
  | 'ALLOCATED'
  | 'ALLOCATION_REVERSED'
  | 'ALLOCATED_BEYOND_DEMAND'
  | 'CHANGE_RECORDED';

/** A recorded field, by its camelCase identifier (the wire name is
 * mapped one to one by the converter). */
export type AuditTrailField =
  | 'name'
  | 'currentRevision'
  | 'erpId'
  | 'image'
  | 'workOrderNumber'
  | 'receivedDate'
  | 'dueDate'
  | 'status'
  | 'requestType'
  | 'requestedQuantity'
  | 'jobNumbers'
  | 'requester'
  | 'reason'
  | 'notes'
  | 'priorityRank';

/** A value as stored (dates stay ISO text; an image is a boolean). */
export type AuditTrailValue = string | number | boolean | string[] | null;

export interface AuditTrailChange {
  field: AuditTrailField;
  before: AuditTrailValue;
  after: AuditTrailValue;
}

/** What the entry is about: the Work Order, the demand line or the
 * Quantity Flow; all null for a Part Number entry. */
export interface AuditTrailSubject {
  workOrderId: number | null;
  /** The Work Order's current number; null for an internal Work Order. */
  workOrderNumber: string | null;
  workOrderDemandId: number | null;
  /** null unless `workOrderDemandId` is set; false: deleted since. */
  demandExists: boolean | null;
  quantityFlowId: number | null;
}

export interface AuditTrailRouteStep {
  sequence: number;
  area: TrackingAreaRef;
  operation: TrackingOperationRef | null;
  /** ISO 8601 duration as sent (the route-adjustments precedent). */
  expectedDuration: string | null;
  /** Retired Machines included. */
  preferredMachine: TrackingMachineRef | null;
  instructions: string | null;
}

export interface AuditTrailPriority {
  action: string | null;
  trigger: string | null;
  removalReason: string | null;
  /** This line only shifted: the action added, removed or moved another
   * entry (every row of one Hot change carries the same action). */
  shifted: boolean;
}

export interface AuditTrailAllocation {
  quantity: number;
  source: 'STOCKROOM' | 'MANAGEMENT';
  isManualOverride: boolean;
  exceedsDemand: boolean;
  reversesAllocationId: number | null;
  stationId: string | null;
}

export interface AuditTrailRoute {
  keptThroughSequence: number;
  /** The replaced tail, ascending. */
  beforeSteps: AuditTrailRouteStep[];
  /** The new tail, ascending. */
  afterSteps: AuditTrailRouteStep[];
}

export interface AuditTrailEntry {
  source: AuditTrailSource;
  id: number;
  occurredAt: string;
  kind: AuditTrailKind;
  /** The User who recorded it; null when none was recorded. */
  actorUser: TrackingUserRef | null;
  /** The recorded text of a row from before sign-in, when present. */
  legacyActor: string | null;
  reason: string | null;
  subject: AuditTrailSubject;
  changes: AuditTrailChange[];
  /** PRIORITY_CHANGED only. */
  priority: AuditTrailPriority | null;
  /** WORK_ORDER_COMPLETED only. */
  completionTrigger: string | null;
  /** Allocation entries only. */
  allocation: AuditTrailAllocation | null;
  /** ROUTE_ADJUSTED only. */
  route: AuditTrailRoute | null;
}

/** The last entry a page delivered — the next page continues below it. */
export interface AuditTrailCursor {
  source: AuditTrailSource;
  id: number;
}

export interface AuditTrailPage {
  partNumber: string;
  /** Newest first. */
  entries: AuditTrailEntry[];
  total: number;
  hasMore: boolean;
  /** null on the last page. */
  next: AuditTrailCursor | null;
}

// ---------------------------------------------------------------------------
// Wire shapes
// ---------------------------------------------------------------------------

type AreaRefWire = Parameters<typeof toArea>[0];
type OperationRefWire = Parameters<typeof toOperation>[0];

interface ChangeWire {
  field: unknown;
  before: unknown;
  after: unknown;
}

interface SubjectWire {
  work_order_id: number | null;
  work_order_number: string | null;
  work_order_demand_id: number | null;
  demand_exists: boolean | null;
  quantity_flow_id: number | null;
}

interface RouteStepWire {
  sequence: number;
  area: AreaRefWire;
  operation: OperationRefWire | null;
  expected_duration: string | null;
  preferred_machine: TrackingMachineRef | null;
  instructions: string | null;
}

interface EntryWire {
  source: unknown;
  id: number;
  occurred_at: string;
  kind: unknown;
  actor_user: unknown;
  legacy_actor: string | null;
  reason: string | null;
  subject: SubjectWire;
  changes: ChangeWire[];
  priority: {
    action: string | null;
    trigger: string | null;
    removal_reason: string | null;
    shifted: boolean;
  } | null;
  completion_trigger: string | null;
  allocation: {
    quantity: number;
    source: unknown;
    is_manual_override: boolean;
    exceeds_demand: boolean;
    reverses_allocation_id: number | null;
    station_id: string | null;
  } | null;
  route: {
    kept_through_sequence: number;
    before_steps: RouteStepWire[];
    after_steps: RouteStepWire[];
  } | null;
}

interface PageWire {
  part_number: string;
  entries: EntryWire[];
  total: number;
  has_more: boolean;
  next_before_source: unknown;
  next_before_id: number | null;
}

// ---------------------------------------------------------------------------
// Mapping
// ---------------------------------------------------------------------------

const SOURCES: readonly AuditTrailSource[] = ['AUDIT', 'ALLOCATION'];

const KINDS: readonly AuditTrailKind[] = [
  'PART_NUMBER_CREATED',
  'PART_NUMBER_UPDATED',
  'PART_NUMBER_IMAGE_CHANGED',
  'PART_NUMBER_DELETED',
  'WORK_ORDER_CREATED',
  'WORK_ORDER_UPDATED',
  'WORK_ORDER_COMPLETED',
  'DEMAND_CREATED',
  'DEMAND_UPDATED',
  'PRIORITY_CHANGED',
  'ROUTE_ADJUSTED',
  'ALLOCATED',
  'ALLOCATION_REVERSED',
  'ALLOCATED_BEYOND_DEMAND',
  'CHANGE_RECORDED',
];

const ALLOCATION_SOURCES: readonly AuditTrailAllocation['source'][] = [
  'STOCKROOM',
  'MANAGEMENT',
];

/** Wire field name → camelCase identifier, one to one. */
const FIELDS: ReadonlyMap<string, AuditTrailField> = new Map([
  ['name', 'name'],
  ['current_revision', 'currentRevision'],
  ['erp_id', 'erpId'],
  ['image', 'image'],
  ['work_order_number', 'workOrderNumber'],
  ['received_date', 'receivedDate'],
  ['due_date', 'dueDate'],
  ['status', 'status'],
  ['request_type', 'requestType'],
  ['requested_quantity', 'requestedQuantity'],
  ['job_numbers', 'jobNumbers'],
  ['requester', 'requester'],
  ['reason', 'reason'],
  ['notes', 'notes'],
  ['priority_rank', 'priorityRank'],
]);

function malformed(): Error {
  return new Error('The server answered a malformed audit trail entry.');
}

function oneOf<T extends string>(value: unknown, known: readonly T[]): T {
  const found = known.find((item) => item === value);
  if (found === undefined) throw malformed();
  return found;
}

function toValue(value: unknown): AuditTrailValue {
  if (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return value;
  }
  if (Array.isArray(value) && value.every((item) => typeof item === 'string')) {
    return value as string[];
  }
  throw malformed();
}

function toChange(wire: ChangeWire): AuditTrailChange {
  const field =
    typeof wire.field === 'string' ? FIELDS.get(wire.field) : undefined;
  if (field === undefined) throw malformed();
  return { field, before: toValue(wire.before), after: toValue(wire.after) };
}

function toRouteStep(wire: RouteStepWire): AuditTrailRouteStep {
  return {
    sequence: wire.sequence,
    area: toArea(wire.area),
    operation: wire.operation ? toOperation(wire.operation) : null,
    expectedDuration: wire.expected_duration,
    preferredMachine: wire.preferred_machine
      ? { id: wire.preferred_machine.id, name: wire.preferred_machine.name }
      : null,
    instructions: wire.instructions,
  };
}

function toEntry(wire: EntryWire): AuditTrailEntry {
  return {
    source: oneOf(wire.source, SOURCES),
    id: wire.id,
    occurredAt: wire.occurred_at,
    kind: oneOf(wire.kind, KINDS),
    actorUser: toAllocationUserRef(wire.actor_user),
    legacyActor: wire.legacy_actor,
    reason: wire.reason,
    subject: {
      workOrderId: wire.subject.work_order_id,
      workOrderNumber: wire.subject.work_order_number,
      workOrderDemandId: wire.subject.work_order_demand_id,
      demandExists: wire.subject.demand_exists,
      quantityFlowId: wire.subject.quantity_flow_id,
    },
    changes: wire.changes.map(toChange),
    priority: wire.priority
      ? {
          action: wire.priority.action,
          trigger: wire.priority.trigger,
          removalReason: wire.priority.removal_reason,
          shifted: wire.priority.shifted,
        }
      : null,
    completionTrigger: wire.completion_trigger,
    allocation: wire.allocation
      ? {
          quantity: wire.allocation.quantity,
          source: oneOf(wire.allocation.source, ALLOCATION_SOURCES),
          isManualOverride: wire.allocation.is_manual_override,
          exceedsDemand: wire.allocation.exceeds_demand,
          reversesAllocationId: wire.allocation.reverses_allocation_id,
          stationId: wire.allocation.station_id,
        }
      : null,
    route: wire.route
      ? {
          keptThroughSequence: wire.route.kept_through_sequence,
          beforeSteps: wire.route.before_steps.map(toRouteStep),
          afterSteps: wire.route.after_steps.map(toRouteStep),
        }
      : null,
  };
}

function toPage(wire: PageWire): AuditTrailPage {
  const next =
    wire.next_before_source === null || wire.next_before_id === null
      ? null
      : {
          source: oneOf(wire.next_before_source, SOURCES),
          id: wire.next_before_id,
        };
  return {
    partNumber: wire.part_number,
    entries: wire.entries.map(toEntry),
    total: wire.total,
    hasMore: wire.has_more,
    next,
  };
}

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

/**
 * One page of the PN's audit trail, newest first: the first page, or —
 * with `before` — the page below the last entry a page delivered. A
 * read: nothing is written.
 */
export async function loadAuditTrail(
  pn: string,
  before?: AuditTrailCursor,
  limit?: number,
): Promise<AuditTrailPage> {
  const params = new URLSearchParams({ part_number: pn });
  if (before) {
    params.set('before_source', before.source);
    params.set('before_id', String(before.id));
  }
  if (limit !== undefined) params.set('limit', String(limit));
  const wire = await apiRequest<PageWire>(
    `/api/tracking/audit-trail?${params.toString()}`,
  );
  return toPage(wire);
}
