// PN audit trail presentation copy (GUI_DESIGN §7.4; Phase 14 slice 7).
//
// The pure helpers that turn one recorded audit trail entry into user
// language: the kind label, the subject (Work Order, demand line or
// Quantity Flow), the field labels and values (no field identifiers),
// the Hot list cause of a priority change, the Work Order completion
// cause, the allocation facts and the replaced / new route steps.
// Exhaustive over the API unions — a new kind or field fails the type
// check instead of rendering an identifier.
//
// Pure: no React, no framework imports.

import type {
  AuditTrailAllocation,
  AuditTrailChange,
  AuditTrailEntry,
  AuditTrailField,
  AuditTrailPriority,
  AuditTrailRoute,
  AuditTrailRouteStep,
  AuditTrailSubject,
  AuditTrailValue,
} from '../../api/audit-trail';
import { operationLabel } from '../area-presentation';
import { formatIsoDateShort } from '../dates';
import { estimateText } from '../planned-routes/route-steps';
import { flowId } from './tracking-logic';

function unreachable(value: never): never {
  throw new Error(`Unhandled audit trail value: ${String(value)}`);
}

/** The image change of an image entry: added, replaced or removed. */
function imageLabel(entry: AuditTrailEntry): string {
  const change = entry.changes.find((item) => item.field === 'image');
  // An image row without a visible boolean difference replaced one
  // custom image by another (both sides present).
  if (change === undefined) return 'Part Number image replaced';
  if (change.before === true && change.after === true) {
    return 'Part Number image replaced';
  }
  return change.after === true
    ? 'Part Number image added'
    : 'Part Number image removed';
}

/** What happened, in one label. */
export function kindLabel(entry: AuditTrailEntry): string {
  switch (entry.kind) {
    case 'PART_NUMBER_CREATED':
      return 'Part Number details created';
    case 'PART_NUMBER_UPDATED':
      return 'Part Number details edited';
    case 'PART_NUMBER_IMAGE_CHANGED':
      return imageLabel(entry);
    case 'PART_NUMBER_DELETED':
      return 'Part Number details deleted';
    case 'WORK_ORDER_CREATED':
      return 'Work Order created';
    case 'WORK_ORDER_UPDATED':
      return 'Work Order edited';
    case 'WORK_ORDER_COMPLETED':
      return `Work Order completed${completionText(entry.completionTrigger)}`;
    case 'DEMAND_CREATED':
      return 'Demand line added';
    case 'DEMAND_UPDATED':
      return 'Demand line edited';
    case 'PRIORITY_CHANGED':
      return 'Priority changed';
    case 'ROUTE_ADJUSTED':
      return 'Route adjusted';
    case 'ALLOCATED':
      return 'Allocated from stock';
    case 'ALLOCATION_REVERSED':
      return 'Allocation reversed';
    case 'ALLOCATED_BEYOND_DEMAND':
      return 'Allocated beyond demand — correction';
    case 'CHANGE_RECORDED':
      return 'Change recorded';
    default:
      return unreachable(entry.kind);
  }
}

/** `WO 007001` — or `WO —` for an internal Work Order. */
function workOrderText(subject: AuditTrailSubject): string {
  return `WO ${subject.workOrderNumber ?? '—'}`;
}

/** What the entry is about; empty for a Part Number entry. */
export function subjectText(subject: AuditTrailSubject): string {
  if (subject.quantityFlowId !== null) {
    return `Quantity Flow ${flowId(subject.quantityFlowId)}`;
  }
  if (subject.workOrderDemandId !== null) {
    const deleted = subject.demandExists === false ? ' (since deleted)' : '';
    return `${workOrderText(subject)} · demand line${deleted}`;
  }
  if (subject.workOrderId !== null) return workOrderText(subject);
  return '';
}

const FIELD_LABELS: Record<AuditTrailField, string> = {
  name: 'Name',
  currentRevision: 'Revision',
  erpId: 'ERP id',
  image: 'Image',
  workOrderNumber: 'Work Order Number',
  receivedDate: 'Received date',
  dueDate: 'Due date',
  status: 'Status',
  requestType: 'Request Type',
  requestedQuantity: 'Requested quantity',
  jobNumbers: 'Job Numbers',
  requester: 'Requester',
  reason: 'Reason',
  notes: 'Notes',
  priorityRank: 'Hot rank',
};

export function fieldLabel(field: AuditTrailField): string {
  return FIELD_LABELS[field];
}

/** One recorded value in user language. */
export function valueText(
  field: AuditTrailField,
  value: AuditTrailValue,
): string {
  if (value === null) {
    if (field === 'dueDate') return 'No due date';
    if (field === 'priorityRank') return 'Not listed';
    return '—';
  }
  switch (field) {
    case 'receivedDate':
    case 'dueDate':
      return formatIsoDateShort(String(value));
    case 'requestedQuantity':
      return `${String(value)} pcs`;
    case 'jobNumbers':
      return Array.isArray(value) && value.length > 0
        ? value.join(', ')
        : Array.isArray(value)
          ? '—'
          : String(value);
    case 'priorityRank':
      return `#${String(value)}`;
    case 'image':
      return value === true ? 'custom image' : 'default image';
    case 'name':
    case 'currentRevision':
    case 'erpId':
    case 'workOrderNumber':
    case 'status':
    case 'requestType':
    case 'requester':
    case 'reason':
    case 'notes':
      return Array.isArray(value) ? value.join(', ') : String(value);
    default:
      return unreachable(field);
  }
}

const CREATED_KINDS: ReadonlySet<AuditTrailEntry['kind']> = new Set([
  'PART_NUMBER_CREATED',
  'WORK_ORDER_CREATED',
  'DEMAND_CREATED',
]);

function changeLine(entry: AuditTrailEntry, change: AuditTrailChange): string {
  const label = fieldLabel(change.field);
  if (entry.kind === 'PART_NUMBER_DELETED') {
    return `${label}: ${valueText(change.field, change.before)}`;
  }
  if (change.before === null && CREATED_KINDS.has(entry.kind)) {
    return `${label}: ${valueText(change.field, change.after)}`;
  }
  return `${label}: ${valueText(change.field, change.before)} → ${valueText(
    change.field,
    change.after,
  )}`;
}

/** One line per recorded change; the `Hot rank` line carries the Hot
 * list cause of a priority change. */
export function changeLines(entry: AuditTrailEntry): string[] {
  return entry.changes.flatMap((change) => {
    // The label of an image entry already says what changed.
    if (
      entry.kind === 'PART_NUMBER_IMAGE_CHANGED' &&
      change.field === 'image'
    ) {
      return [];
    }
    const line = changeLine(entry, change);
    return change.field === 'priorityRank' && entry.priority !== null
      ? [`${line}${priorityText(entry.priority)}`]
      : [line];
  });
}

const HOT_ACTIONS: ReadonlyMap<string, string> = new Map([
  ['ADD', 'added'],
  ['REMOVE', 'removed'],
  ['MOVE_UP', 'moved up'],
  ['MOVE_DOWN', 'moved down'],
  ['DRAG', 'dragged to a new position'],
  ['UNDO', 'previous ranking restored'],
  ['REDO', 'ranking reapplied'],
  ['AUTO_REMOVE', 'removed automatically'],
  ['LINE_DELETE', 'demand line deleted'],
]);

const REMOVAL_REASONS: ReadonlyMap<string, string> = new Map([
  ['FULLY_ALLOCATED', 'fully allocated'],
  ['WORK_ORDER_COMPLETED', 'Work Order completed'],
  ['LINE_DELETED', 'line deleted'],
]);

const TRIGGERS: ReadonlyMap<string, string> = new Map([
  ['ALLOCATION', 'an allocation'],
  ['WORK_ORDER_SAVE', 'a Work Order save'],
  ['DEMAND_LINE_REMOVAL', 'a demand line deletion'],
]);

function triggerText(trigger: string): string {
  return ` (after ${TRIGGERS.get(trigger) ?? 'another change'})`;
}

/** The Hot list cause appended to the `Hot rank` line. */
export function priorityText(priority: AuditTrailPriority): string {
  const action =
    priority.action === null
      ? 'changed'
      : (HOT_ACTIONS.get(priority.action) ?? 'changed');
  const removal =
    priority.removalReason === null
      ? ''
      : ` — ${REMOVAL_REASONS.get(priority.removalReason) ?? 'no longer active'}`;
  const trigger =
    priority.trigger === null ? '' : triggerText(priority.trigger);
  return ` · Hot list: ${action}${removal}${trigger}`;
}

/** The cause of a Work Order completion, appended to its label. */
export function completionText(trigger: string | null): string {
  return trigger === null ? '' : triggerText(trigger);
}

/** The allocation facts of an allocation entry. */
export function allocationText(
  allocation: AuditTrailAllocation,
  subject: AuditTrailSubject,
): string {
  return [
    `${allocation.quantity} pcs · ${workOrderText(subject)}`,
    allocation.exceedsDemand ? ' · beyond demand' : '',
    allocation.isManualOverride ? ' · manual override' : '',
    allocation.reversesAllocationId !== null
      ? ` · reverses allocation #${allocation.reversesAllocationId}`
      : '',
    allocation.stationId ? ` · ${allocation.stationId}` : '',
  ].join('');
}

function stepLine(step: AuditTrailRouteStep): string {
  return [
    `${step.sequence}. ${step.area.name}`,
    step.operation ? ` · ${operationLabel(step.operation)}` : '',
    step.expectedDuration !== null
      ? ` · Est. ${estimateText(step.expectedDuration)}`
      : '',
    step.preferredMachine ? ` · Machine ${step.preferredMachine.name}` : '',
    step.instructions ? ` · Instructions: ${step.instructions}` : '',
  ].join('');
}

function stepSide(
  heading: string,
  steps: readonly AuditTrailRouteStep[],
): string[] {
  return [
    heading,
    ...(steps.length === 0 ? ['no further steps'] : steps.map(stepLine)),
  ];
}

/** The replaced steps, then the new ones. */
export function routeLines(route: AuditTrailRoute): string[] {
  const k = route.keptThroughSequence;
  return [
    ...stepSide(`Steps after step ${k} — before:`, route.beforeSteps),
    ...stepSide(`Steps after step ${k} — now:`, route.afterSteps),
  ];
}

export function reasonText(reason: string): string {
  return `Reason: ${reason}`;
}
