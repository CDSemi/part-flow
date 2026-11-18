// Permanent deletion requests API (Phase 16, history archival design D17;
// archival build step 3): Delete permanently… in Work Order Details and in
// the Part Number dialog, and the Permanent deletion requests card of
// Administration → History archival & purge.
//
// What a deletion would delete (the preview: the Work Orders and Part
// Numbers, the rows per table and the audit rows kept with the reason each
// is kept — or every refusal — and the open request, if one exists), the
// request (the request is the approval), its cancellation and the requests,
// newest first. The server records a request and the host job carries it
// out: APPROVED (waiting), RUNNING (the pre-purge backup, then the deletion
// in one transaction), then COMPLETED or FAILED (nothing deleted); a request
// cancelled before the job starts it is CANCELLED. Every route needs the key
// Delete Work Orders and Part Numbers permanently.
//
// The server owns every rule — the scope, the reason, the typed
// confirmation, the one open request and the refusals — and this module
// only maps the wire shape, throwing on a malformed answer instead of
// showing a wrong one.
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequest, refusalFlag } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** The states of a request: `APPROVED` and `RUNNING` are open (at most one
 * request is open), the others final. */
export type DeletionRequestState =
  'APPROVED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

const DELETION_REQUEST_STATES: readonly DeletionRequestState[] = [
  'APPROVED',
  'RUNNING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
];

/** What a deletion names: Work Order Numbers exactly as stored, or one
 * Part Number (the server's canonical form). */
export interface DeletionScope {
  workOrderNumbers: string[];
  partNumbers: string[];
}

/** What the UI asks to delete: Work Orders by their numbers, or one Part
 * Number with all its data. */
export type DeletionTarget =
  | { kind: 'work-orders'; workOrderNumbers: readonly string[] }
  | { kind: 'part-number'; partNumber: string };

/** One Work Order a deletion deletes. */
export interface DeletionWorkOrder {
  id: number;
  /** Null for an internal Work Order (no Work Order Number). */
  workOrderNumber: string | null;
  internal: boolean;
  /** Named in the request (false: deleted with the Part Numbers of all
   * its lines). */
  named: boolean;
  partNumbers: string[];
  demandLines: number;
}

/** One Part Number a deletion deletes; `master` = it has saved details. */
export interface DeletionPartNumber {
  partNumber: string;
  master: boolean;
}

/** The rows a deletion deletes, per database table. */
export type DeletionRowCounts = Readonly<Record<string, number>>;

/** An audit row that names deleted data but stays, and why it stays. */
export interface KeptAuditRow {
  id: number;
  entityType: string;
  entityId: string;
  eventType: string;
  occurredAt: string | null;
  /** The reason it is kept, as the server words it. */
  kept: string;
}

/** One reason a deletion is refused, as the server words it. */
export interface DeletionRefusal {
  code: string;
  message: string;
}

export interface DeletionRequestUser {
  userId: number;
  displayName: string;
}

/** One permanent deletion request. */
export interface DeletionRequest {
  id: number;
  state: DeletionRequestState;
  scope: DeletionScope;
  reason: string;
  /** The requesting User; null for an operator's request. */
  requestedBy: DeletionRequestUser | null;
  /** The operator who requested it on the server (`pf
   * delete-permanently`); null for a User's request. */
  requestedByOperator: string | null;
  requestedAt: string;
  /** What the request's plan deletes: its records and rows per table. */
  workOrders: DeletionWorkOrder[];
  partNumbers: DeletionPartNumber[];
  rows: DeletionRowCounts;
  startedAt: string | null;
  backupLabel: string | null;
  /** The pre-purge backup the host job took before the deletion. */
  prePurgeBackup: { name: string; dumpStartedAt: string } | null;
  finishedAt: string | null;
  cancelledBy: DeletionRequestUser | null;
  /** Set exactly when the request is `FAILED`: nothing was deleted. */
  failureReason: string | null;
}

/** What a deletion would delete, or why it is refused. */
export interface DeletionPreview {
  scope: DeletionScope;
  workOrders: DeletionWorkOrder[];
  partNumbers: DeletionPartNumber[];
  /** Null when the deletion is refused. */
  rows: DeletionRowCounts | null;
  /** Empty when the deletion is refused. */
  keptAuditRows: KeptAuditRow[];
  refusals: DeletionRefusal[];
  /** The open request, if one exists: a request now would be refused. */
  openRequest: DeletionRequest | null;
}

// ---------------------------------------------------------------------------
// Wire mapping (checked)
// ---------------------------------------------------------------------------

type Wire = Record<string, unknown>;

function malformed(): Error {
  return new Error('The server answered malformed permanent deletion data.');
}

function record(value: unknown): Wire {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw malformed();
  }
  return value as Wire;
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw malformed();
  return value;
}

function int(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw malformed();
  return value;
}

function count(value: unknown): number {
  const found = int(value);
  if (found < 0) throw malformed();
  return found;
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw malformed();
  return value;
}

function textOrNull(value: unknown): string | null {
  return value === null ? null : text(value);
}

function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw malformed();
  return value;
}

function oneOf<T extends string>(value: unknown, known: readonly T[]): T {
  const found = known.find((item) => item === value);
  if (found === undefined) throw malformed();
  return found;
}

function toScope(value: unknown): DeletionScope {
  const wire = record(value);
  return {
    workOrderNumbers: list(wire.work_order_numbers).map(text),
    partNumbers: list(wire.part_numbers).map(text),
  };
}

function toWorkOrder(value: unknown): DeletionWorkOrder {
  const wire = record(value);
  return {
    id: int(wire.id),
    workOrderNumber: textOrNull(wire.work_order_number),
    internal: flag(wire.internal),
    named: flag(wire.named),
    partNumbers: list(wire.part_numbers).map(text),
    demandLines: count(wire.demand_lines),
  };
}

function toPartNumber(value: unknown): DeletionPartNumber {
  const wire = record(value);
  return { partNumber: text(wire.part_number), master: flag(wire.master) };
}

function toRows(value: unknown): DeletionRowCounts {
  const wire = record(value);
  return Object.fromEntries(
    Object.entries(wire).map(([table, rows]) => [table, count(rows)]),
  );
}

function toKeptAuditRow(value: unknown): KeptAuditRow {
  const wire = record(value);
  return {
    id: int(wire.id),
    entityType: text(wire.entity_type),
    entityId: text(wire.entity_id),
    eventType: text(wire.event_type),
    occurredAt: textOrNull(wire.occurred_at),
    kept: text(wire.kept),
  };
}

function toRefusal(value: unknown): DeletionRefusal {
  const wire = record(value);
  return { code: text(wire.code), message: text(wire.message) };
}

function toUser(value: unknown): DeletionRequestUser | null {
  if (value === null) return null;
  const wire = record(value);
  return { userId: int(wire.user_id), displayName: text(wire.display_name) };
}

function toRequest(value: unknown): DeletionRequest {
  const wire = record(value);
  const backup =
    wire.pre_purge_backup === null ? null : record(wire.pre_purge_backup);
  return {
    id: int(wire.id),
    state: oneOf(wire.state, DELETION_REQUEST_STATES),
    scope: toScope(wire.scope),
    reason: text(wire.reason),
    requestedBy: toUser(wire.requested_by),
    requestedByOperator: textOrNull(wire.requested_by_operator),
    requestedAt: text(wire.requested_at),
    workOrders: list(wire.work_orders).map(toWorkOrder),
    partNumbers: list(wire.part_numbers).map(toPartNumber),
    rows: toRows(wire.rows),
    startedAt: textOrNull(wire.started_at),
    backupLabel: textOrNull(wire.backup_label),
    prePurgeBackup:
      backup === null
        ? null
        : {
            name: text(backup.name),
            dumpStartedAt: text(backup.dump_started_at),
          },
    finishedAt: textOrNull(wire.finished_at),
    cancelledBy: toUser(wire.cancelled_by),
    failureReason: textOrNull(wire.failure_reason),
  };
}

function toPreview(value: unknown): DeletionPreview {
  const wire = record(value);
  return {
    scope: toScope(wire.scope),
    workOrders: list(wire.work_orders).map(toWorkOrder),
    partNumbers: list(wire.part_numbers).map(toPartNumber),
    rows: wire.rows === null ? null : toRows(wire.rows),
    keptAuditRows: list(wire.kept_audit_rows).map(toKeptAuditRow),
    refusals: list(wire.refusals).map(toRefusal),
    openRequest:
      wire.open_request === null ? null : toRequest(wire.open_request),
  };
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

const BASE_PATH = '/api/permanent-deletion';

function scopeQuery(target: DeletionTarget): string {
  return target.kind === 'part-number'
    ? `part_number=${encodeURIComponent(target.partNumber)}`
    : target.workOrderNumbers
        .map((number) => `work_order_number=${encodeURIComponent(number)}`)
        .join('&');
}

/** What deleting `target` would delete, every refusal, and the open
 * request; reads only. */
export async function previewDeletion(
  target: DeletionTarget,
): Promise<DeletionPreview> {
  return toPreview(
    await apiRequest<unknown>(`${BASE_PATH}/preview?${scopeQuery(target)}`),
  );
}

/** Request the deletion of `target` with the reason and the typed
 * confirmation (the Work Order Numbers, or the Part Number, typed again —
 * sent as typed; the server checks them). Answers the new request
 * (`APPROVED`). */
export async function requestDeletion(
  target: DeletionTarget,
  reason: string,
  confirmation: readonly string[],
): Promise<DeletionRequest> {
  const scope =
    target.kind === 'part-number'
      ? { part_number: target.partNumber }
      : { work_order_numbers: [...target.workOrderNumbers] };
  return toRequest(
    await apiRequest<unknown>(`${BASE_PATH}/requests`, {
      method: 'POST',
      body: { ...scope, reason, confirmation: [...confirmation] },
    }),
  );
}

/** Cancel an `APPROVED` request (a cancelled request answers unchanged). */
export async function cancelDeletionRequest(
  requestId: number,
): Promise<DeletionRequest> {
  return toRequest(
    await apiRequest<unknown>(`${BASE_PATH}/requests/${requestId}/cancel`, {
      method: 'POST',
    }),
  );
}

/** The newest requests first (the server's default limit when none is
 * given). */
export async function listDeletionRequests(
  limit?: number,
): Promise<DeletionRequest[]> {
  const query = limit === undefined ? '' : `?limit=${limit}`;
  return list(await apiRequest<unknown>(`${BASE_PATH}/requests${query}`)).map(
    toRequest,
  );
}

/**
 * The refusals of a request the server refused because its plan refuses
 * (409 with `deletion_refused` and every refusal), or null for any other
 * failure — nothing was requested either way. A refusal list that is
 * malformed leaves the server's message alone (null).
 */
export function deletionRefusals(error: unknown): DeletionRefusal[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  if (!refusalFlag(error, 'deletion_refused')) return null;
  try {
    return list((error.body as Wire).refusals).map(toRefusal);
  } catch {
    return null;
  }
}
