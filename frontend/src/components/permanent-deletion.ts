// Presentation of the permanent deletion requests (Phase 16, history
// archival design D17; archival build step 3), shared by Delete
// permanently… (Work Order Details and the Part Number dialog of
// Management → Part Numbers) and the Permanent deletion requests card of
// Administration → History archival & purge: the plain words of each
// request state, the records a request names, the rows per table, who
// requested it and its times. Presentation only — the server owns every
// rule and decides every transition; the actions offered here follow the
// states it answers.

import type {
  DeletionRequest,
  DeletionRequestState,
  DeletionRowCounts,
  DeletionScope,
  DeletionWorkOrder,
  KeptAuditRow,
} from '../api/permanent-deletion';
import { formatInstant } from '../views/administration/archive-runs';

/** The one record Delete permanently… deletes: the Work Order of Work
 * Order Details, or the Part Number of the Part Number dialog. */
export type PermanentDeletionRecord =
  | { kind: 'work-order'; workOrderNumber: string }
  | { kind: 'part-number'; partNumber: string };

/** Whether `request` names exactly this one record and nothing else: it
 * is then this record's own request, which the dialog follows. */
export function requestNamesOnly(
  request: DeletionRequest,
  record: PermanentDeletionRecord,
): boolean {
  const { workOrderNumbers, partNumbers } = request.scope;
  return record.kind === 'work-order'
    ? partNumbers.length === 0 &&
        workOrderNumbers.length === 1 &&
        workOrderNumbers[0] === record.workOrderNumber
    : workOrderNumbers.length === 0 &&
        partNumbers.length === 1 &&
        partNumbers[0] === record.partNumber;
}

/** How often Delete permanently… reads the state of the request it shows
 * while that request is open. */
export const DELETION_POLL_MS = 3000;

/** The newest requests one poll reads: an open request is always among
 * them, since no other request can be made while one is open. */
export const DELETION_POLL_LIMIT = 10;

/** The one sentence beside a request that waits for the server ("normally":
 * the task may not be set up on the server yet). */
export const DELETION_WAIT_NOTE =
  "The server's history archive task carries it out, normally within 5 minutes.";

/** The plain words of a request's state. */
export function deletionStateLabel(state: DeletionRequestState): string {
  switch (state) {
    case 'APPROVED':
      return 'Waiting for the server';
    case 'RUNNING':
      return 'Deleting…';
    case 'COMPLETED':
      return 'Deleted';
    case 'FAILED':
      return 'Failed — nothing was deleted';
    case 'CANCELLED':
      return 'Cancelled';
  }
}

/** The status-pill tone of a request's state (the tones of the archival
 * runs). */
export function deletionStateTone(
  state: DeletionRequestState,
): 'on' | 'off' | 'term' | 'err' {
  switch (state) {
    case 'COMPLETED':
      return 'on';
    case 'FAILED':
      return 'err';
    case 'APPROVED':
    case 'RUNNING':
      return 'term';
    case 'CANCELLED':
      return 'off';
  }
}

/** The server has not finished the request yet (it waits or deletes). */
export function deletionRequestIsOpen(request: DeletionRequest): boolean {
  return request.state === 'APPROVED' || request.state === 'RUNNING';
}

/** A request the server has not started may be cancelled — never one it
 * works on; the server decides and a refusal is shown. */
export function deletionRequestCancellable(request: DeletionRequest): boolean {
  return request.state === 'APPROVED';
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString('en-US')} ${noun}${count === 1 ? '' : 's'}`;
}

/** The records a request names: the kind of each, and its Work Order
 * Number or Part Number. */
export function scopeRecords(
  scope: DeletionScope,
): { kind: 'Work Order' | 'Part Number'; name: string }[] {
  return [
    ...scope.workOrderNumbers.map((name) => ({
      kind: 'Work Order' as const,
      name,
    })),
    ...scope.partNumbers.map((name) => ({
      kind: 'Part Number' as const,
      name,
    })),
  ];
}

/** The records a request names, one per line: `Work Order 007201`,
 * `Part Number A-100`. */
export function scopeLines(scope: DeletionScope): string[] {
  return scopeRecords(scope).map(({ kind, name }) => `${kind} ${name}`);
}

/** The longest Work Order Number or Part Number the requests table keeps
 * on one line; a longer one may break anywhere, like an archive name, so
 * that it never widens the table past its card. */
export const WHOLE_NAME_MAX_LENGTH = 16;

/** The Work Orders a request deletes without naming them (internal Work
 * Orders deleted with the Part Numbers of all their lines): `Also 1
 * internal Work Order`; null when there is none. */
export function unnamedWorkOrdersLine(
  workOrders: readonly DeletionWorkOrder[],
): string | null {
  const unnamed = workOrders.filter((workOrder) => !workOrder.named).length;
  return unnamed === 0
    ? null
    : `Also ${plural(unnamed, 'internal Work Order')}`;
}

/** `3 demand lines: A-100, B-200, E-500`, after `Internal Work Order ·`
 * for a Work Order without a number (it displays `—`). */
export function workOrderLinesText(workOrder: DeletionWorkOrder): string {
  const lines = plural(workOrder.demandLines, 'demand line');
  const text =
    workOrder.partNumbers.length === 0
      ? lines
      : `${lines}: ${workOrder.partNumbers.join(', ')}`;
  return workOrder.workOrderNumber === null
    ? `Internal Work Order · ${text}`
    : text;
}

/** Who requested a request: the User, or the operator on the server. */
export function requesterName(request: DeletionRequest): string {
  if (request.requestedBy !== null) return request.requestedBy.displayName;
  if (request.requestedByOperator !== null) {
    return `${request.requestedByOperator} (operator)`;
  }
  return '—';
}

/** The rows a deletion deletes, in the order the screens list them, each
 * table with its plain name; a table the server adds later keeps its own
 * name. */
const TABLE_LABELS: readonly (readonly [string, string])[] = [
  ['work_orders', 'Work Orders'],
  ['work_order_demands', 'Demand lines'],
  ['work_order_allocations', 'Allocations'],
  ['quantity_flows', 'Quantity Flows'],
  ['quantity_flow_lineage', 'Lineage links'],
  ['part_movements', 'Movements'],
  ['assigned_routes', 'Assigned Routes'],
  ['assigned_route_steps', 'Assigned Route steps'],
  ['part_numbers', 'Part Number details'],
  ['audit_events', 'Audit rows'],
];

export function rowsPerTable(
  rows: DeletionRowCounts,
): { table: string; label: string; count: number }[] {
  const known = new Set(TABLE_LABELS.map(([table]) => table));
  return [
    ...TABLE_LABELS.filter(([table]) => table in rows).map(
      ([table, label]) => ({ table, label, count: rows[table] }),
    ),
    ...Object.keys(rows)
      .filter((table) => !known.has(table))
      .map((table) => ({ table, label: table, count: rows[table] })),
  ];
}

/** `86 rows`: every row the deletion deletes. */
export function totalRowsText(rows: DeletionRowCounts): string {
  return plural(
    Object.values(rows).reduce((sum, value) => sum + value, 0),
    'row',
  );
}

/** One kept audit row: `WorkOrderDemand 5 · UPDATED · Nov 17, 2026 08:00`
 * (the audit trail's own names, as the server answers them). */
export function keptAuditRowText(row: KeptAuditRow): string {
  return [
    `${row.entityType} ${row.entityId}`,
    row.eventType,
    ...(row.occurredAt === null ? [] : [formatInstant(row.occurredAt)]),
  ].join(' · ');
}

/** The times a request reached, oldest first. */
export function deletionRequestTimes(
  request: DeletionRequest,
): { label: string; at: string }[] {
  const times: { label: string; at: string | null }[] = [
    { label: 'Requested', at: request.requestedAt },
    { label: 'Started', at: request.startedAt },
    {
      label: 'Backup started',
      at: request.prePurgeBackup?.dumpStartedAt ?? null,
    },
    { label: 'Finished', at: request.finishedAt },
  ];
  return times.filter(
    (time): time is { label: string; at: string } => time.at !== null,
  );
}

/** The latest step a request reached after its request (Finished or
 * Started), or null while it waits. */
export function latestDeletionStep(
  request: DeletionRequest,
): { label: string; at: string } | null {
  if (request.finishedAt !== null) {
    return { label: 'Finished', at: request.finishedAt };
  }
  if (request.startedAt !== null) {
    return { label: 'Started', at: request.startedAt };
  }
  return null;
}

/** What a finished request came to, in plain words: the failure reason
 * as the server gives it; null while it is open. */
export function deletionOutcome(request: DeletionRequest): string | null {
  switch (request.state) {
    case 'COMPLETED':
      return 'Deleted from the database.';
    case 'FAILED':
      return request.failureReason ?? 'Failed.';
    case 'CANCELLED':
      return request.cancelledBy === null
        ? 'Cancelled.'
        : `Cancelled by ${request.cancelledBy.displayName}.`;
    default:
      return null;
  }
}
