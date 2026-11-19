// PN Tracking presentation logic (framework-independent).
//
// The pure helpers behind the real Tracking view: the feed cadence and
// page bounds, the status pill vocabulary, the location rows of the
// `Current quantity by Area` section, the position line of a Quantity
// Flow block, the audit-facing description of one Movement history
// entry (which the archive viewer of History archival & purge shares, so
// that archived history reads as live history does), and the archived
// history (Phase 16): the "History archived in run N" line and its place
// among the Movements of a page. Everything
// here reads the fixed server data — derived time values take the shared
// UI clock tick as an argument (§3.12), and no archived quantity is ever
// computed here.

import type {
  LocationState,
  TrackingArchivedHistory,
  TrackingDetail,
  TrackingFilters,
  TrackingFlow,
  TrackingLocation,
  TrackingMovement,
  TrackingMovementPage,
  TrackingStatus,
} from '../../api/tracking';
import { DEFAULT_TRACKING_FILTERS } from '../../api/tracking';
import {
  exceedsExpectedDuration,
  formatElapsedSince,
  formatIsoDate,
  formatTimeOfDay,
  formatTimestampShort,
  todayIso,
} from '../dates';

/** Refresh period of the list and of an open detail — the monitoring
 * cadence every live view shares. */
export const TRACKING_REFRESH_MS = 15_000;

/** Rows of the first list page; `Show more` doubles it once, up to the
 * server's bound (200) — beyond that the user narrows the search. */
export const TRACKING_PAGE_SIZE = 100;
export const TRACKING_MAX_ROWS = 200;

/** Movements per history page (the detail's first page and each
 * `Show older Movements` continuation). */
export const MOVEMENTS_PAGE_SIZE = 50;
/** Scrap events per Scrap history page. */
export const SCRAP_PAGE_SIZE = 20;
/** Quantity Flows per page (the detail's first page and each `Show
 * older Quantity Flows` continuation — one immutable order: newest
 * first on the flow id, status being presentation only). */
export const FLOWS_PAGE_SIZE = 50;
/** Allocation entries per continuation page. */
export const ALLOCATIONS_PAGE_SIZE = 100;

/** Debounce of the search field before it reaches the server. */
export const SEARCH_DEBOUNCE_MS = 250;

/** The revision signature of each paged detail section (`useOlderPages`). */
export interface DetailRevisions {
  movements: string;
  scrap: string;
  flows: string;
  allocations: string;
}

/**
 * The figures of one polled detail whose change means rows appended
 * BELOW a section's first page may now read differently, so the
 * appended pages are read again — deliberately narrow per section, so
 * a refresh that changes none of them keeps the pages as they are:
 *
 * - Movement history: its row count and its archived-entry count — every
 *   write to the PN appends a Movement, and one dated below the boundary
 *   lands among the older pages without moving the first page; an
 *   archival purge replaces a flow's Movements with one archived entry.
 * - Scrap history: its row count, its archived-entry count and the net
 *   scrapped quantity — an undone scrap keeps its place in the history
 *   while the net figure changes and the row gains its REVERSED mark.
 * - Quantity Flows: the flow count, the Movement and archived-entry
 *   counts and the route adjustment count — a flow's status, position
 *   and trace only ever change through a Movement of the PN or a purge of
 *   its history, its route steps and route notes only through an
 *   AssignedRoute adjustment or a purge, and the flow that changed may
 *   sit on any page.
 * - Allocation history: its row count — allocation rows are append-only,
 *   a reversal being a new row beside the one it takes back.
 */
export function detailRevisions(detail: TrackingDetail): DetailRevisions {
  const { movements, scrapHistory } = detail;
  return {
    movements: `${movements.total}|${movements.archivedTotal}`,
    scrap: `${scrapHistory.total}|${scrapHistory.archivedTotal}|${detail.scrappedQuantity}`,
    flows: `${detail.flows.total}|${movements.total}|${movements.archivedTotal}|${detail.routeAdjustmentTotal}`,
    allocations: `${detail.allocations.total}`,
  };
}

export function filtersAreDefault(filters: TrackingFilters): boolean {
  return (
    filters.search.trim() === '' &&
    filters.areaId === null &&
    filters.operationId === null &&
    filters.machineId === null &&
    filters.requestType === null &&
    !filters.hotOnly &&
    filters.status === DEFAULT_TRACKING_FILTERS.status &&
    filters.due === DEFAULT_TRACKING_FILTERS.due
  );
}

// ---------------------------------------------------------------------------
// Status pill
// ---------------------------------------------------------------------------

export const STATUS_LABEL: Record<TrackingStatus, string> = {
  ACTIVE: 'Active',
  STOCKED: 'Stocked',
  OPEN: 'Open',
  COMPLETED: 'Completed',
};

/** Pill tone class (styles/global.css `.status.*`). */
export const STATUS_CLASS: Record<TrackingStatus, string> = {
  ACTIVE: 'active',
  STOCKED: 'stocked',
  OPEN: 'queued',
  COMPLETED: 'done',
};

export const STATUS_TITLE: Record<TrackingStatus, string> = {
  ACTIVE: 'Quantity in production',
  STOCKED:
    'No quantity in production — unallocated stocked quantity is available to the open demand',
  OPEN: 'Open Work Order Demand — no quantity in production and no unallocated stock',
  COMPLETED: 'No open Work Order Demand — history only',
};

// ---------------------------------------------------------------------------
// Locations
// ---------------------------------------------------------------------------

/** The presentation of one location row: name, sub-label, tone. */
export interface LocationRowView {
  name: string;
  sub: string;
  tone: 'machine' | 'queue' | 'processing' | 'done' | 'stocked';
}

const STATE_SUB: Record<LocationState, string> = {
  MACHINE: 'on machine',
  QUEUE: 'queue',
  PROCESSING: 'processing',
  DONE: 'ready to transfer',
  STOCKED: 'stocked',
};

export function locationRow(location: TrackingLocation): LocationRowView {
  const tone = location.state.toLowerCase() as LocationRowView['tone'];
  if (location.state === 'MACHINE' && location.machine) {
    return { name: location.machine.name, sub: STATE_SUB.MACHINE, tone };
  }
  if (location.state === 'PROCESSING' && location.activity) {
    return { name: location.area.name, sub: location.activity, tone };
  }
  return { name: location.area.name, sub: STATE_SUB[location.state], tone };
}

/** Bar width (percent of the PN's whole quantity in production and in
 * stock) — the quantity itself is always printed beside the bar. */
export function locationPercent(quantity: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(2, Math.round((quantity / total) * 100));
}

/** The operator wording of a finished (READY_TO_TRANSFER) position. */
export function readyNote(locations: TrackingLocation[]): string | null {
  const done = locations.filter((location) => location.state === 'DONE');
  if (done.length === 0) return null;
  return done
    .map((location) => {
      const where = location.machine
        ? `${location.machine.name} in ${location.area.name}`
        : location.area.name;
      return `Completed processing at ${where} — ready to transfer (${location.quantity} pcs wait in ${location.area.name} until transferred).`;
    })
    .join(' ');
}

// ---------------------------------------------------------------------------
// Quantity Flows
// ---------------------------------------------------------------------------

export const FLOW_STATUS_LABEL: Record<string, string> = {
  ACTIVE: 'active',
  SPLIT: 'split — consumed',
  MERGED: 'merged — consumed',
  SCRAPPED: 'scrapped — closed',
  STOCKED: 'stocked — complete',
  REVERSED: 'reversed — never active',
};

/** The local date and time of one history entry (`Jul 24 08:15`). */
export function timestamp(iso: string): string {
  return formatTimestampShort(iso);
}

export function flowId(id: number): string {
  return `QF-${id}`;
}

/** The lineage statement of a flow block (`SPLIT from QF-3`, …). */
export function lineageText(flow: TrackingFlow): string | null {
  if (flow.parents.length === 0) return null;
  const relation = flow.parents[0].relation;
  const parents = flow.parents.map((link) => flowId(link.quantityFlowId));
  return `${relation} from ${parents.join(' + ')}`;
}

/** The position line of a flow block, derived at render from the fixed
 * entry timestamp and the shared UI clock tick. */
export function positionText(flow: TrackingFlow, nowMs: number): string {
  const parts: string[] = [];
  if (flow.position) {
    const { area, machine, activity, state, since, expectedBy } = flow.position;
    const row = locationRow({
      area,
      machine,
      activity,
      quantity: flow.quantity,
      state,
      since,
      expectedBy,
    });
    parts.push(
      state === 'MACHINE'
        ? `${area.name} · ${row.name}`
        : `${area.name} · ${row.sub}`,
    );
    parts.push(`${formatElapsedSince(since, nowMs)} in Area`);
  } else {
    parts.push(FLOW_STATUS_LABEL[flow.status] ?? flow.status.toLowerCase());
  }
  const lineage = lineageText(flow);
  if (lineage) parts.push(lineage);
  if (flow.children.length > 0) {
    const relation = flow.children[0].relation;
    const children = flow.children.map((link) => flowId(link.quantityFlowId));
    parts.push(`${relation} into ${children.join(' + ')}`);
  }
  return parts.join(' · ');
}

/** Whether an ACTIVE flow's position is past its effective expected
 * duration (PROJECT_PROFILE §17) — the server's fixed `expectedBy`
 * judged by the shared UI clock; never without one. Advisory. */
export function positionOverdue(flow: TrackingFlow, nowMs: number): boolean {
  return (
    flow.position !== null &&
    exceedsExpectedDuration(flow.position.expectedBy, nowMs)
  );
}

/** The rendered steps of a flow's route line: the PLANNED snapshot, or
 * the actual trace of a FLOATING flow (its last arrival current while
 * the flow is active). An archived flow's snapshot steps read `archived`:
 * its progress is in the archive, neither done nor still to come. */
export interface RouteStepView {
  key: string;
  label: string;
  state: 'done' | 'cur' | 'future' | 'archived';
  repair: boolean;
  inherited: boolean;
}

const ROUTE_STEP_STATE: Record<
  TrackingFlow['routeSteps'][number]['state'],
  RouteStepView['state']
> = {
  DONE: 'done',
  CURRENT: 'cur',
  FUTURE: 'future',
  ARCHIVED: 'archived',
};

export function routeSteps(flow: TrackingFlow): RouteStepView[] {
  if (flow.routeMode === 'PLANNED') {
    return flow.routeSteps.map((step) => ({
      key: `snap-${step.id}`,
      label: step.area.name,
      state: ROUTE_STEP_STATE[step.state] ?? 'future',
      repair: false,
      inherited: false,
    }));
  }
  // The current step is the last arrival only when the server's derived
  // position still stands in that Area — the position is the server's
  // fact, never an index assumption about the trace.
  const last = flow.trace.length - 1;
  return flow.trace.map((step, index) => ({
    key: `trace-${step.movementId}`,
    label: step.area.name,
    state:
      index === last && flow.position?.area.id === step.area.id
        ? 'cur'
        : 'done',
    repair: step.repair,
    inherited: step.inherited,
  }));
}

/** `Material → Lathe → Cut` — the actual path of a PLANNED flow, shown
 * beside its snapshot. */
export function traceText(flow: TrackingFlow): string {
  return flow.trace
    .map((step) => `${step.area.name}${step.repair ? ' ⟲ REPAIR' : ''}`)
    .join(' → ');
}

// ---------------------------------------------------------------------------
// Movement history
// ---------------------------------------------------------------------------

/** Badge tone class of a Movement type (tracking.css `.mtype.*`). */
export function movementTypeClass(movementType: string): string {
  switch (movementType) {
    case 'RECEIVED':
    case 'QUANTITY_ADJUSTED':
      return 'rec';
    case 'TRANSFERRED':
      return 'tra';
    case 'ASSIGNED_TO_MACHINE':
    case 'RELEASED_FROM_MACHINE':
      return 'asg';
    case 'AREA_COMPLETED':
      return 'don';
    case 'SPLIT':
    case 'MERGED':
      return 'spl';
    case 'SCRAPPED':
      return 'scr';
    case 'REVERSED':
      return 'rev';
    case 'STOCKED':
      return 'stk';
    default:
      return '';
  }
}

function woText(workOrderNumber: string | null): string {
  return `WO ${workOrderNumber ?? '—'}`;
}

/** What `describeMovement` reads of a Movement: a live one of Tracking,
 * or an archived one of the archive viewer of History archival & purge
 * (its names as they were at export, resolved by the caller). */
export interface DescribedMovement {
  quantityFlowId: number;
  movementType: string;
  quantity: number;
  fromArea: { name: string } | null;
  toArea: { name: string };
  sourceMachine: { name: string } | null;
  destinationMachine: { name: string } | null;
  worker: { name: string } | null;
  stationId: string | null;
  movementReason: string | null;
  reason: string | null;
  reversesMovementId: number | null;
  assignedRouteStep: { sequence: number } | null;
  routeDeviation: Record<string, unknown> | null;
  lineage: readonly { parentFlowId: number; childFlowId: number }[];
  demand: { workOrderNumber: string | null } | null;
}

/**
 * The audit-facing description of one Movement: Areas, quantity,
 * Quantity Flow, Machines, recorded Worker, station, reasons and
 * relationships — canonical recorded data, never an interpretation.
 */
export function describeMovement(movement: DescribedMovement): string {
  const qf = flowId(movement.quantityFlowId);
  const qty = `qty ${movement.quantity}`;
  const to = movement.toArea.name;
  const from = movement.fromArea?.name ?? '';
  const parts: string[] = [];
  switch (movement.movementType) {
    case 'RECEIVED': {
      parts.push(`Received into ${to}`, qty, qf);
      if (movement.demand) {
        parts.push(`${woText(movement.demand.workOrderNumber)} release`);
      }
      if (movement.assignedRouteStep) {
        parts.push(`Planned Route step ${movement.assignedRouteStep.sequence}`);
      }
      break;
    }
    case 'TRANSFERRED':
    case 'STOCKED': {
      parts.push(`${from} → ${to}`, qty, qf);
      if (movement.movementType === 'STOCKED') parts.push('stocked');
      if (movement.movementReason === 'REPAIR') {
        parts.push(`Repair — reason: ${movement.reason ?? '—'}`);
      }
      if (movement.assignedRouteStep) {
        parts.push(`Planned Route step ${movement.assignedRouteStep.sequence}`);
      }
      if (movement.routeDeviation) {
        const reason = movement.routeDeviation.reason;
        parts.push(
          `route deviation confirmed${typeof reason === 'string' && reason ? ` — reason: ${reason}` : ''}`,
        );
      }
      break;
    }
    case 'ASSIGNED_TO_MACHINE':
      parts.push(
        `${to} queue → ${movement.destinationMachine?.name ?? '—'}`,
        qty,
        qf,
      );
      break;
    case 'RELEASED_FROM_MACHINE':
      parts.push(
        `${movement.sourceMachine?.name ?? '—'} → ${to} queue`,
        qty,
        qf,
      );
      break;
    case 'AREA_COMPLETED': {
      const where = movement.sourceMachine
        ? `${movement.sourceMachine.name} in ${to}`
        : to;
      parts.push(
        `Completed processing at ${where} — ready to transfer`,
        qty,
        qf,
      );
      break;
    }
    case 'SPLIT':
    case 'MERGED': {
      const parents = movement.lineage
        .filter((edge) => edge.childFlowId === movement.quantityFlowId)
        .map((edge) => flowId(edge.parentFlowId));
      const children = movement.lineage
        .filter((edge) => edge.parentFlowId === movement.quantityFlowId)
        .map((edge) => flowId(edge.childFlowId));
      if (children.length > 0) {
        parts.push(`${qf} (${movement.quantity}) → ${children.join(' + ')}`);
      } else if (parents.length > 0) {
        parts.push(`${qf} (${movement.quantity}) from ${parents.join(' + ')}`);
      } else {
        parts.push(qf, qty);
      }
      parts.push(`at ${to}`);
      break;
    }
    case 'SCRAPPED':
      parts.push(`Scrapped ${movement.quantity} at ${to}`, qf);
      if (movement.reason) parts.push(`reason: ${movement.reason}`);
      break;
    case 'QUANTITY_ADJUSTED':
      parts.push(`Added ${movement.quantity} at ${to}`, qf);
      if (movement.reason) parts.push(`reason: ${movement.reason}`);
      break;
    case 'REVERSED':
      parts.push(
        `Reverses Movement #${movement.reversesMovementId ?? '—'}`,
        qty,
        qf,
        `at ${to}`,
      );
      if (movement.reason) parts.push(`reason: ${movement.reason}`);
      break;
    default:
      parts.push(`${from ? `${from} → ` : ''}${to}`, qty, qf);
  }
  if (movement.worker) parts.push(`W: ${movement.worker.name}`);
  if (movement.stationId) parts.push(movement.stationId);
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------
// Archived history (Phase 16 — history archival step 2)
// ---------------------------------------------------------------------------

/** One row of the Movement history or the Scrap history: a retained
 * Movement, or the archived history of one flow ("History archived in run
 * N"), which stands where its newest archived Movement stood. */
export type HistoryEntry =
  | { kind: 'movement'; movement: TrackingMovement }
  | { kind: 'archived'; archived: TrackingArchivedHistory };

/** An ISO 8601 instant as a sortable pair: its whole second (epoch
 * milliseconds) and the fraction of that second in nanoseconds — instants
 * a microsecond apart keep their order, which `Date.parse` alone
 * (milliseconds) would not. */
function instantKey(iso: string): [number, number] {
  const match = /^(.+T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$/.exec(iso);
  if (match === null) return [Date.parse(iso), 0];
  const [, whole, fraction = '', zone] = match;
  return [
    Date.parse(`${whole}${zone}`),
    Number(fraction.slice(0, 9).padEnd(9, '0')),
  ];
}

/** Newest first, as the server orders the history: the later time, then
 * the higher id. */
function newestFirst(
  a: { at: string; id: number },
  b: { at: string; id: number },
): number {
  const [aSecond, aFraction] = instantKey(a.at);
  const [bSecond, bFraction] = instantKey(b.at);
  return bSecond - aSecond || bFraction - aFraction || b.id - a.id;
}

/**
 * The rows of one history page, newest first: its Movements and its
 * archived entries interleaved by (time, id) — a Movement by
 * `(occurredAt, id)`, an archived entry by `(lastOccurredAt,
 * lastMovementId)`. The server chose the page from both kinds together,
 * so consecutive pages concatenate without a gap or a duplicate.
 */
export function historyEntries(page: TrackingMovementPage): HistoryEntry[] {
  const keyed: { entry: HistoryEntry; at: string; id: number }[] = [
    ...page.movements.map((movement) => ({
      entry: { kind: 'movement' as const, movement },
      at: movement.occurredAt,
      id: movement.id,
    })),
    ...page.archived.map((archived) => ({
      entry: { kind: 'archived' as const, archived },
      at: archived.lastOccurredAt,
      id: archived.lastMovementId,
    })),
  ];
  return keyed.sort(newestFirst).map(({ entry }) => entry);
}

/** The React key of a history row (a Movement id and an archived entry
 * never share one). */
export function historyEntryKey(entry: HistoryEntry): string {
  return entry.kind === 'movement'
    ? `m-${entry.movement.id}`
    : `a-${entry.archived.runId}-${entry.archived.quantityFlowId}`;
}

/** The local date of an instant, with its year: `Jan 03, 2023`. */
function localDate(iso: string): string {
  return formatIsoDate(todayIso(Date.parse(iso)));
}

/** The time column of an archived entry, always with its year (`Nov 18,
 * 2023 02:06`): archived history is older than the retention period, so
 * the short `Nov 18 02:06` of the other rows would read as this year. */
export function archivedTimestamp(iso: string): string {
  return `${localDate(iso)} ${formatTimeOfDay(iso)}`;
}

/** `History archived in run 3 (Jan 03, 2023 – Nov 16, 2024, 12
 * Movements)` — one date when the flow's history began and ended on the
 * same day. The numbers are the server's record, never recomputed. */
export function archivedHistoryText(entry: TrackingArchivedHistory): string {
  const first = localDate(entry.firstOccurredAt);
  const last = localDate(entry.lastOccurredAt);
  const range = first === last ? first : `${first} – ${last}`;
  const count = `${entry.movementCount.toLocaleString('en-US')} Movement${entry.movementCount === 1 ? '' : 's'}`;
  return `History archived in run ${entry.runId} (${range}, ${count})`;
}

/** The description of an archived history row: the line, the flow and
 * the archive's name. */
export function describeArchivedHistory(
  entry: TrackingArchivedHistory,
): string {
  const parts = [archivedHistoryText(entry), flowId(entry.quantityFlowId)];
  if (entry.archiveName !== null) parts.push(entry.archiveName);
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------
// Codes kept whole
// ---------------------------------------------------------------------------

/** The longest code a line keeps whole; a longer one may still break at
 * its hyphens, so that it never widens a narrow panel. */
export const WHOLE_CODE_MAX_LENGTH = 24;

/** One run of a text: plain words, or a code kept whole. */
export interface TextRun {
  text: string;
  code: boolean;
}

/**
 * A description cut into its plain text and its codes — every run of
 * non-space characters with a hyphen inside (`QF-44`, `ST-PACK`,
 * `E2E-WO-1`, a Part Number), up to `WHOLE_CODE_MAX_LENGTH` characters —
 * so that a narrow line wraps between words and never after a code's
 * hyphen (`ST-` / `PACK`). The runs join back into the text unchanged.
 */
export function codeRuns(text: string): TextRun[] {
  const runs: TextRun[] = [];
  let last = 0;
  for (const match of text.matchAll(/\S*[^\s-]-[^\s-]\S*/g)) {
    const code = match[0];
    if (code.length > WHOLE_CODE_MAX_LENGTH) continue;
    const start = match.index ?? 0;
    if (start > last) runs.push({ text: text.slice(last, start), code: false });
    runs.push({ text: code, code: true });
    last = start + code.length;
  }
  if (last < text.length) runs.push({ text: text.slice(last), code: false });
  return runs;
}
