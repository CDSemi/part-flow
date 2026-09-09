// PN Tracking presentation logic (framework-independent).
//
// The pure helpers behind the real Tracking view: the feed cadence and
// page bounds, the status pill vocabulary, the location rows of the
// `Current quantity by Area` section, the position line of a Quantity
// Flow block, and the audit-facing description of one Movement history
// entry. Everything here reads the fixed server data — derived time
// values take the shared UI clock tick as an argument (§3.12).

import type {
  LocationState,
  TrackingDetail,
  TrackingFilters,
  TrackingFlow,
  TrackingLocation,
  TrackingMovement,
  TrackingStatus,
} from '../../api/tracking';
import { DEFAULT_TRACKING_FILTERS } from '../../api/tracking';
import { formatElapsedSince } from '../dates';

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
 * older Quantity Flows` continuation — one order: ACTIVE flows oldest
 * first, then closed flows newest first). */
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
 * - Movement history: its row count — every write to the PN appends a
 *   Movement, and one dated below the boundary lands among the older
 *   pages without moving the first page.
 * - Scrap history: its row count and the net scrapped quantity — an
 *   undone scrap keeps its place in the history while the net figure
 *   changes and the row gains its REVERSED mark.
 * - Quantity Flows: the flow count and the Movement count — a flow's
 *   status, position and trace only ever change through a Movement of
 *   the PN, and the flow that closed or reopened may sit on any page.
 * - Allocation history: its row count — allocation rows are append-only,
 *   a reversal being a new row beside the one it takes back.
 */
export function detailRevisions(detail: TrackingDetail): DetailRevisions {
  return {
    movements: `${detail.movements.total}`,
    scrap: `${detail.scrapHistory.total}|${detail.scrappedQuantity}`,
    flows: `${detail.flows.total}|${detail.movements.total}`,
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
    const { area, machine, activity, state, since } = flow.position;
    const row = locationRow({
      area,
      machine,
      activity,
      quantity: flow.quantity,
      state,
      since,
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

/** The rendered steps of a flow's route line: the PLANNED snapshot, or
 * the actual trace of a FLOATING flow (its last arrival current while
 * the flow is active). */
export interface RouteStepView {
  key: string;
  label: string;
  state: 'done' | 'cur' | 'future';
  repair: boolean;
  inherited: boolean;
}

export function routeSteps(flow: TrackingFlow): RouteStepView[] {
  if (flow.routeMode === 'PLANNED') {
    return flow.routeSteps.map((step) => ({
      key: `snap-${step.id}`,
      label: step.area.name,
      state:
        step.state === 'DONE'
          ? 'done'
          : step.state === 'CURRENT'
            ? 'cur'
            : 'future',
      repair: false,
      inherited: false,
    }));
  }
  const last = flow.trace.length - 1;
  return flow.trace.map((step, index) => ({
    key: `trace-${step.movementId}`,
    label: step.area.name,
    state: index === last && flow.position !== null ? 'cur' : 'done',
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

/**
 * The audit-facing description of one Movement: Areas, quantity,
 * Quantity Flow, Machines, station, reasons and relationships —
 * canonical recorded data, never an interpretation.
 */
export function describeMovement(movement: TrackingMovement): string {
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
  if (movement.stationId) parts.push(movement.stationId);
  return parts.join(' · ');
}
