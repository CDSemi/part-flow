// The archive viewer of Administration → History archival & purge
// (Phase 16, archival design D16, build step 5; GUI_DESIGN §9): the page
// that reads one run's archive loaded for viewing — a read-only copy kept
// apart from live history. Framework-independent: what the page loads
// before it can search (its view and the names to search by), the search
// form's terms, the names as they were at export, and the presentation of
// an archived Movement and flow as Tracking presents live history
// (`tracking-logic`). The server owns every rule — which views can be
// read, how each term matches, the paging — and its refusals are shown as
// given; nothing here decides what a Movement means.

import { ApiError } from '../../api/client';
import {
  listArchiveViews,
  loadArchiveViewNames,
} from '../../api/history-archive-views';
import type {
  ArchivedFlow,
  ArchivedMovement,
  ArchivedMovementSearch,
  ArchivedName,
  ArchivedOperation,
  ArchivedWorkOrder,
  ArchiveView,
  ArchiveViewNames,
} from '../../api/history-archive-views';
import {
  describeMovement,
  FLOW_STATUS_LABEL,
  flowId,
} from '../tracking/tracking-logic';
import { formatInstant, movementsText } from './archive-runs';

/** Movements per page of the results (the first page and each `Show older
 * Movements`). */
export const VIEWER_PAGE_SIZE = 50;

/** The views read to find the page's own: the most the server lists (its
 * newest views first). */
export const VIEWER_VIEWS_LIMIT = 500;

/** What the page needs before it can search — its view (null when the
 * server's newest views do not include it) and the names to search by —
 * or why the view cannot be read, as the server words it. */
export type ViewerContext =
  | { kind: 'open'; view: ArchiveView | null; names: ArchiveViewNames }
  | { kind: 'unavailable'; view: ArchiveView | null; message: string };

/** What the page says of the copy it reads, under its mark: the archive,
 * when the copy was loaded and until when it stays open, and what it
 * holds (`view` null when the server's newest views do not include it). */
export function viewCopyText(view: ArchiveView | null): string {
  if (view === null) return 'A copy of an archive loaded for viewing.';
  const parts = [
    `A copy of the archive ${view.toLoad.archiveName ?? `of run ${view.runId}`}, loaded for viewing`,
  ];
  if (view.openedAt !== null) parts.push(` on ${formatInstant(view.openedAt)}`);
  if (view.openUntil !== null) {
    parts.push(` and open until ${formatInstant(view.openUntil)}`);
  }
  if (view.loaded !== null) {
    const flows = view.loaded.flows;
    parts.push(
      `: ${movementsText(view.loaded.movements)} in ${flows.toLocaleString('en-US')} flow${flows === 1 ? '' : 's'}`,
    );
  }
  return `${parts.join('')}.`;
}

/** A viewer read refused because the view is not open — not loaded yet,
 * closed, or ended (409, its text says which). */
export function viewNotOpen(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409;
}

/** Read the page's view and its names together; a view that is unknown
 * (404) or not open (409) is a state of the page, any other failure an
 * error with Retry. */
export async function loadViewerContext(
  viewId: number,
): Promise<ViewerContext> {
  const names = loadArchiveViewNames(viewId).then(
    (value) => ({ ok: true as const, value }),
    (error: unknown) => ({ ok: false as const, error }),
  );
  const views = await listArchiveViews(VIEWER_VIEWS_LIMIT);
  const view = views.find((entry) => entry.id === viewId) ?? null;
  const answer = await names;
  if (answer.ok) return { kind: 'open', view, names: answer.value };
  const { error } = answer;
  if (
    error instanceof ApiError &&
    (error.status === 404 || error.status === 409)
  ) {
    return { kind: 'unavailable', view, message: error.message };
  }
  throw error;
}

// ---------------------------------------------------------------------------
// The search form
// ---------------------------------------------------------------------------

/** The search form as typed; a select holds a record's id, `` for all. */
export interface SearchForm {
  partNumber: string;
  workOrderNumber: string;
  flow: string;
  areaId: string;
  operationId: string;
  machineId: string;
  workerId: string;
  dateFrom: string;
  dateTo: string;
}

export const EMPTY_SEARCH_FORM: SearchForm = {
  partNumber: '',
  workOrderNumber: '',
  flow: '',
  areaId: '',
  operationId: '',
  machineId: '',
  workerId: '',
  dateFrom: '',
  dateTo: '',
};

/** The one refusal the form makes itself. */
export const FLOW_NUMBER_ERROR =
  'Enter a flow number, such as 5512 or QF-5512.';

/** The flow typed as Tracking writes it (`QF-5512`) or by its number
 * (`5512`); null for a blank field, `invalid` for anything else (the
 * server checks the range). */
export function parseFlowNumber(text: string): number | null | 'invalid' {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const match = /^(?:QF-?)?(\d+)$/i.exec(trimmed);
  if (match === null) return 'invalid';
  const value = Number(match[1]);
  return Number.isSafeInteger(value) ? value : 'invalid';
}

/** The search the form asks for: every filled term — a blank field asks
 * for nothing; the Part Number goes as typed (the server makes it
 * canonical) and the Work Order Number exactly as typed, since it matches
 * the number as stored — or the form's own refusal. */
export function searchOf(
  form: SearchForm,
): { search: ArchivedMovementSearch } | { error: string } {
  const flow = parseFlowNumber(form.flow);
  if (flow === 'invalid') return { error: FLOW_NUMBER_ERROR };
  const search: ArchivedMovementSearch = {};
  if (form.partNumber.trim() !== '') search.partNumber = form.partNumber;
  if (form.workOrderNumber.trim() !== '') {
    search.workOrderNumber = form.workOrderNumber;
  }
  if (flow !== null) search.quantityFlowId = flow;
  if (form.areaId !== '') search.areaId = Number(form.areaId);
  if (form.operationId !== '') search.operationId = Number(form.operationId);
  if (form.machineId !== '') search.machineId = Number(form.machineId);
  if (form.workerId !== '') search.workerId = Number(form.workerId);
  if (form.dateFrom !== '') search.dateFrom = form.dateFrom;
  if (form.dateTo !== '') search.dateTo = form.dateTo;
  return { search };
}

/** Whether a search asks for every Movement of the view. */
export function searchIsEmpty(search: ArchivedMovementSearch): boolean {
  return Object.keys(search).length === 0;
}

// ---------------------------------------------------------------------------
// Names as they were at export
// ---------------------------------------------------------------------------

/** An Area by its archived name; one the archive does not name reads by
 * its number. */
export function areaName(area: ArchivedName): string {
  return area.name ?? `Area ${area.id}`;
}

export function machineName(machine: ArchivedName): string {
  return machine.name ?? `Machine ${machine.id}`;
}

export function workerName(worker: ArchivedName): string {
  return worker.name ?? `Worker ${worker.id}`;
}

/** An Operation as Tracking names one: its name, else its code. */
export function operationName(operation: ArchivedOperation): string {
  return operation.name ?? operation.code ?? `Operation ${operation.id}`;
}

// ---------------------------------------------------------------------------
// Archived Movements and flows, as Tracking presents live history
// ---------------------------------------------------------------------------

/**
 * The audit-facing description of an archived Movement — Tracking's own
 * (`describeMovement`), with the names the Movement carried at export —
 * and the User who recorded a Management command (`by <name>`). A route
 * step the archive does not hold is left out.
 */
export function describeArchivedMovement(movement: ArchivedMovement): string {
  const step = movement.assignedRouteStep;
  const text = describeMovement({
    quantityFlowId: movement.quantityFlowId,
    movementType: movement.movementType,
    quantity: movement.quantity,
    fromArea:
      movement.fromArea === null ? null : { name: areaName(movement.fromArea) },
    toArea: { name: areaName(movement.toArea) },
    sourceMachine:
      movement.sourceMachine === null
        ? null
        : { name: machineName(movement.sourceMachine) },
    destinationMachine:
      movement.destinationMachine === null
        ? null
        : { name: machineName(movement.destinationMachine) },
    worker:
      movement.worker === null ? null : { name: workerName(movement.worker) },
    stationId: movement.stationId,
    movementReason: movement.movementReason,
    reason: movement.reason,
    reversesMovementId: movement.reversesMovementId,
    assignedRouteStep:
      step === null || step.sequence === null
        ? null
        : { sequence: step.sequence },
    routeDeviation: movement.routeDeviation,
    lineage: movement.lineage,
    demand: movement.demand,
  });
  const user = movement.actorUser;
  if (user === null) return text;
  return `${text} · by ${user.displayName ?? `User ${user.userId}`}`;
}

/** The status line of an archived flow, as Tracking writes a closed
 * flow's: its status, then its effective lineage (`SPLIT from QF-3`,
 * `SPLIT into QF-7 + QF-8`); an undone split or merge is listed apart. */
export function flowStatusLine(flow: ArchivedFlow): string {
  const parts = [FLOW_STATUS_LABEL[flow.status] ?? flow.status.toLowerCase()];
  const parents = flow.parents.filter((link) => !link.void);
  if (parents.length > 0) {
    parts.push(
      `${parents[0].relation} from ${parents.map((link) => flowId(link.quantityFlowId)).join(' + ')}`,
    );
  }
  const children = flow.children.filter((link) => !link.void);
  if (children.length > 0) {
    parts.push(
      `${children[0].relation} into ${children.map((link) => flowId(link.quantityFlowId)).join(' + ')}`,
    );
  }
  return parts.join(' · ');
}

/** A whole number of the carry-forward record, else null. */
function recordCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

/** The Work Orders a flow was released for: the numbered ones by their
 * numbers, the others (internal Work Orders, which have no Work Order
 * Number) counted — `Work Order WO-1`, `Work Orders WO-1 and 2 internal
 * Work Orders`, `1 internal Work Order`; null for none. */
export function workOrdersText(
  workOrders: readonly ArchivedWorkOrder[],
): string | null {
  if (workOrders.length === 0) return null;
  const numbers = workOrders
    .map((workOrder) => workOrder.workOrderNumber)
    .filter((number): number is string => number !== null);
  const internal = workOrders.length - numbers.length;
  const internalText = `${internal} internal Work Order${internal === 1 ? '' : 's'}`;
  if (numbers.length === 0) return internalText;
  const named = `${workOrders.length === 1 ? 'Work Order' : 'Work Orders'} ${numbers.join(', ')}`;
  return internal === 0 ? named : `${named} and ${internalText}`;
}

/** What the archive adds about a flow, line by line: the Work Orders it
 * was released for (directly or through the flows it came from), what
 * its record says was stocked or scrapped and where, and when it was
 * created and closed. */
export function flowRecordLines(flow: ArchivedFlow): string[] {
  const lines: string[] = [];
  const workOrders = workOrdersText(flow.workOrders);
  if (workOrders !== null) lines.push(workOrders);
  const stocked = recordCount(flow.record.stocked_quantity);
  if (stocked !== null) {
    lines.push(
      flow.stockedArea === null
        ? `Stocked ${stocked}`
        : `Stocked ${stocked} at ${areaName(flow.stockedArea)}`,
    );
  }
  const scrapped = recordCount(flow.record.scrapped_quantity);
  if (scrapped !== null) {
    lines.push(
      flow.scrappedArea === null
        ? `Scrapped ${scrapped}`
        : `Scrapped ${scrapped} at ${areaName(flow.scrappedArea)}`,
    );
  }
  lines.push(
    flow.closedAt === null
      ? `Created ${formatInstant(flow.createdAt)}`
      : `Created ${formatInstant(flow.createdAt)} · closed ${formatInstant(flow.closedAt)}`,
  );
  return lines;
}
