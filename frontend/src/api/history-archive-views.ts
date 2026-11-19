// Viewing an archive API (Administration → History archival & purge;
// Phase 16, archival design D16, build step 5).
//
// A holder of Run history archival and purge opens the verified archive of
// a finished run for viewing (a view, `REQUESTED`). The server's archival
// job loads a copy of the archive files into viewer tables of their own,
// never into live history (`LOADING`, then `OPEN`). Close viewing
// (`CLOSING`), or the end of the days the setting allows, has the job
// remove the loaded rows (`CLOSED`); a waiting view may be cancelled
// (`CANCELLED`), and a load that fails ends `FAILED` with its reason. The
// viewer reads an `OPEN` view only: a search of its Movements (newest
// first, paged by the id of the last Movement delivered, as Tracking pages
// a history), one flow's whole archived history with its lineage and
// route, and the names to search by — every name as it was when the
// archive was written. Every route needs the key. The run object's
// `viewable` and `view` (`api/history-archive.ts`) are decoded here too.
//
// The server owns every rule — which runs can be opened, one active view
// per run, the runs loaded at once, the states and every refusal — and
// this module only maps the wire shape, throwing on a malformed answer
// instead of showing a wrong one.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** The states of a view: `REQUESTED`, `LOADING` and `OPEN` are active (at
 * most one per run, and at most the setting's number at once), `CLOSING`
 * waits for the job to remove the loaded rows, the others are final. */
export type ArchiveViewState =
  | 'REQUESTED'
  | 'LOADING'
  | 'OPEN'
  | 'CLOSING'
  | 'CLOSED'
  | 'FAILED'
  | 'CANCELLED';

export const ARCHIVE_VIEW_STATES: readonly ArchiveViewState[] = [
  'REQUESTED',
  'LOADING',
  'OPEN',
  'CLOSING',
  'CLOSED',
  'FAILED',
  'CANCELLED',
];

export interface ArchiveViewUser {
  userId: number;
  displayName: string;
}

/** What the view's run exported: what a load brings into the database. */
export interface ArchiveViewExport {
  archiveName: string | null;
  movements: number;
  flows: number;
  lineageEdges: number;
  assignedRoutes: number;
  archiveBytes: number;
}

/** The rows a load wrote, per kind. */
export interface ArchiveViewLoaded {
  movements: number;
  flows: number;
  lineageEdges: number;
  assignedRoutes: number;
  names: number;
}

/** One view of a run's archive. */
export interface ArchiveView {
  id: number;
  runId: number;
  state: ArchiveViewState;
  requestedBy: ArchiveViewUser;
  requestedAt: string;
  toLoad: ArchiveViewExport;
  startedAt: string | null;
  openedAt: string | null;
  /** When its days run out (the setting as it stands; it moves with the
   * setting); null until the view opened. */
  openUntil: string | null;
  /** Null until the view opened. */
  loaded: ArchiveViewLoaded | null;
  closingStartedAt: string | null;
  /** The person who closed it; null when its days ran out. */
  closedBy: ArchiveViewUser | null;
  cancelledBy: ArchiveViewUser | null;
  finishedAt: string | null;
  /** Set exactly when the view is `FAILED`, as the server words it. */
  failureReason: string | null;
}

/** The newest view of a run (any state), as the run object names it. */
export interface ArchiveRunView {
  id: number;
  state: ArchiveViewState;
}

/** A record the archive names (an Area, a Machine, a Worker) as it was at
 * export; `name` is null when the archive does not name it. */
export interface ArchivedName {
  id: number;
  name: string | null;
}

export interface ArchivedOperation {
  id: number;
  code: string | null;
  name: string | null;
}

/** The demand line a `RECEIVED` released, with its Work Order. */
export interface ArchivedDemand {
  workOrderDemandId: number;
  workOrderId: number | null;
  workOrderNumber: string | null;
  requestType: string | null;
}

export interface ArchivedWorkOrder {
  workOrderId: number;
  workOrderNumber: string | null;
}

/** One lineage edge of a `SPLIT` or `MERGED` row; `void` = its split or
 * merge was undone. */
export interface ArchivedLineageEdge {
  parentFlowId: number;
  childFlowId: number;
  relation: string;
  void: boolean;
}

/** One archived Movement with the names it carried at export. */
export interface ArchivedMovement {
  id: number;
  quantityFlowId: number;
  partNumber: string;
  movementType: string;
  quantity: number;
  fromArea: ArchivedName | null;
  toArea: ArchivedName;
  operation: ArchivedOperation;
  sourceMachine: ArchivedName | null;
  destinationMachine: ArchivedName | null;
  stationId: string | null;
  worker: ArchivedName | null;
  occurredAt: string;
  deviceEventId: string;
  commandSequence: number;
  movementReason: string | null;
  reason: string | null;
  /** Set on a `REVERSED` row: the original it undoes. */
  reversesMovementId: number | null;
  /** Set on an original that was undone: the `REVERSED` row. */
  reversedByMovementId: number | null;
  /** The route step it recorded; `sequence` is null when the archive holds
   * no such step. */
  assignedRouteStep: { id: number; sequence: number | null } | null;
  routeDeviation: Record<string, unknown> | null;
  lineage: ArchivedLineageEdge[];
  demand: ArchivedDemand | null;
  /** The signed-in User of a Management command, as named at export. */
  actorUser: { userId: number; displayName: string | null } | null;
}

/** One page of a search, newest first. */
export interface ArchivedMovementPage {
  movements: ArchivedMovement[];
  /** Every Movement of the view that matches the search. */
  total: number;
  hasMore: boolean;
  /** Pass as `before` for the next (older) page; null on the last page. */
  nextBeforeMovementId: number | null;
}

/** A parent or child of an archived flow; `void` = undone. */
export interface ArchivedFlowLink {
  quantityFlowId: number;
  relation: string;
  void: boolean;
}

export interface ArchivedRouteStep {
  id: number;
  sequence: number;
  area: ArchivedName;
  operation: ArchivedOperation | null;
  preferredMachine: ArchivedName | null;
  expectedDuration: string | null;
  instructions: string | null;
}

/** An archived flow's Assigned Route snapshot. */
export interface ArchivedRoute {
  assignedRouteId: number;
  sourceRouteTemplateId: number | null;
  createdAt: string | null;
  steps: ArchivedRouteStep[];
}

/** One archived flow: its carry-forward record, lineage, route and whole
 * history (newest first). */
export interface ArchivedFlow {
  id: number;
  partNumber: string;
  /** The Part Number's name at export; null without one. */
  partNumberName: string | null;
  componentId: number;
  quantity: number;
  status: string;
  routeMode: string;
  createdAt: string;
  closedAt: string | null;
  /** The carry-forward record exactly as the archive holds it. */
  record: Readonly<Record<string, unknown>>;
  stockedArea: ArchivedName | null;
  scrappedArea: ArchivedName | null;
  demand: ArchivedDemand | null;
  /** The Work Orders it was released for, directly or through the flows
   * it was split or merged from. */
  workOrders: ArchivedWorkOrder[];
  parents: ArchivedFlowLink[];
  children: ArchivedFlowLink[];
  route: ArchivedRoute | null;
  movements: ArchivedMovement[];
}

/** What a view's Movements can be searched by, sorted by id. */
export interface ArchiveViewNames {
  areas: ArchivedName[];
  operations: ArchivedOperation[];
  machines: ArchivedName[];
  workers: ArchivedName[];
}

/** A search of a view's Movements; every term given is combined with AND,
 * and each is checked by the server. */
export interface ArchivedMovementSearch {
  /** Canonicalized by the server like every Part Number input. */
  partNumber?: string;
  /** The exact Work Order Number as stored. */
  workOrderNumber?: string;
  quantityFlowId?: number;
  /** Either end of a Movement. */
  areaId?: number;
  operationId?: number;
  /** The source or the destination Machine. */
  machineId?: number;
  workerId?: number;
  /** Site-calendar days (`YYYY-MM-DD`), both included. */
  dateFrom?: string;
  dateTo?: string;
}

// ---------------------------------------------------------------------------
// Wire mapping (checked)
// ---------------------------------------------------------------------------

type Wire = Record<string, unknown>;

function malformed(): Error {
  return new Error('The server answered malformed archive viewing data.');
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

function intOrNull(value: unknown): number | null {
  return value === null ? null : int(value);
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

/** `map(value)`, or null for an explicit null (a missing field throws). */
function nullable<T>(value: unknown, map: (value: unknown) => T): T | null {
  return value === null ? null : map(value);
}

function toUser(value: unknown): ArchiveViewUser {
  const wire = record(value);
  return { userId: int(wire.user_id), displayName: text(wire.display_name) };
}

function toExport(value: unknown): ArchiveViewExport {
  const wire = record(value);
  return {
    archiveName: textOrNull(wire.archive_name),
    movements: count(wire.movements),
    flows: count(wire.flows),
    lineageEdges: count(wire.lineage_edges),
    assignedRoutes: count(wire.assigned_routes),
    archiveBytes: count(wire.archive_bytes),
  };
}

function toLoaded(value: unknown): ArchiveViewLoaded {
  const wire = record(value);
  return {
    movements: count(wire.movements),
    flows: count(wire.flows),
    lineageEdges: count(wire.lineage_edges),
    assignedRoutes: count(wire.assigned_routes),
    names: count(wire.names),
  };
}

function toView(value: unknown): ArchiveView {
  const wire = record(value);
  return {
    id: int(wire.id),
    runId: int(wire.run_id),
    state: oneOf(wire.state, ARCHIVE_VIEW_STATES),
    requestedBy: toUser(wire.requested_by),
    requestedAt: text(wire.requested_at),
    toLoad: toExport(wire.to_load),
    startedAt: textOrNull(wire.started_at),
    openedAt: textOrNull(wire.opened_at),
    openUntil: textOrNull(wire.open_until),
    loaded: nullable(wire.loaded, toLoaded),
    closingStartedAt: textOrNull(wire.closing_started_at),
    closedBy: nullable(wire.closed_by, toUser),
    cancelledBy: nullable(wire.cancelled_by, toUser),
    finishedAt: textOrNull(wire.finished_at),
    failureReason: textOrNull(wire.failure_reason),
  };
}

/** The run object's `viewable` (Open for viewing applies: a finished run
 * whose archive was verified) and `view` (its newest view, or null). */
export function toRunViewing(
  viewable: unknown,
  view: unknown,
): { viewable: boolean; view: ArchiveRunView | null } {
  return {
    viewable: flag(viewable),
    view: nullable(view, (value) => {
      const wire = record(value);
      return {
        id: int(wire.id),
        state: oneOf(wire.state, ARCHIVE_VIEW_STATES),
      };
    }),
  };
}

function toName(value: unknown): ArchivedName {
  const wire = record(value);
  return { id: int(wire.id), name: textOrNull(wire.name) };
}

function toOperation(value: unknown): ArchivedOperation {
  const wire = record(value);
  return {
    id: int(wire.id),
    code: textOrNull(wire.code),
    name: textOrNull(wire.name),
  };
}

function toDemand(value: unknown): ArchivedDemand {
  const wire = record(value);
  return {
    workOrderDemandId: int(wire.work_order_demand_id),
    workOrderId: intOrNull(wire.work_order_id),
    workOrderNumber: textOrNull(wire.work_order_number),
    requestType: textOrNull(wire.request_type),
  };
}

function toWorkOrder(value: unknown): ArchivedWorkOrder {
  const wire = record(value);
  return {
    workOrderId: int(wire.work_order_id),
    workOrderNumber: textOrNull(wire.work_order_number),
  };
}

function toLineageEdge(value: unknown): ArchivedLineageEdge {
  const wire = record(value);
  return {
    parentFlowId: int(wire.parent_flow_id),
    childFlowId: int(wire.child_flow_id),
    relation: text(wire.relation),
    void: flag(wire.void),
  };
}

function toMovement(value: unknown): ArchivedMovement {
  const wire = record(value);
  return {
    id: int(wire.id),
    quantityFlowId: int(wire.quantity_flow_id),
    partNumber: text(wire.part_number),
    movementType: text(wire.movement_type),
    quantity: int(wire.quantity),
    fromArea: nullable(wire.from_area, toName),
    toArea: toName(wire.to_area),
    operation: toOperation(wire.operation),
    sourceMachine: nullable(wire.source_machine, toName),
    destinationMachine: nullable(wire.destination_machine, toName),
    stationId: textOrNull(wire.station_id),
    worker: nullable(wire.worker, toName),
    occurredAt: text(wire.occurred_at),
    deviceEventId: text(wire.device_event_id),
    commandSequence: int(wire.command_sequence),
    movementReason: textOrNull(wire.movement_reason),
    reason: textOrNull(wire.reason),
    reversesMovementId: intOrNull(wire.reverses_movement_id),
    reversedByMovementId: intOrNull(wire.reversed_by_movement_id),
    assignedRouteStep: nullable(wire.assigned_route_step, (step) => {
      const stepWire = record(step);
      return { id: int(stepWire.id), sequence: intOrNull(stepWire.sequence) };
    }),
    routeDeviation: nullable(wire.route_deviation, record),
    lineage: list(wire.lineage).map(toLineageEdge),
    demand: nullable(wire.demand, toDemand),
    actorUser: nullable(wire.actor_user, (user) => {
      const userWire = record(user);
      return {
        userId: int(userWire.user_id),
        displayName: textOrNull(userWire.display_name),
      };
    }),
  };
}

function toMovementPage(value: unknown): ArchivedMovementPage {
  const wire = record(value);
  return {
    movements: list(wire.movements).map(toMovement),
    total: count(wire.total),
    hasMore: flag(wire.has_more),
    nextBeforeMovementId: intOrNull(wire.next_before_movement_id),
  };
}

function toFlowLink(value: unknown): ArchivedFlowLink {
  const wire = record(value);
  return {
    quantityFlowId: int(wire.quantity_flow_id),
    relation: text(wire.relation),
    void: flag(wire.void),
  };
}

function toRouteStep(value: unknown): ArchivedRouteStep {
  const wire = record(value);
  return {
    id: int(wire.id),
    sequence: int(wire.sequence),
    area: toName(wire.area),
    operation: nullable(wire.operation, toOperation),
    preferredMachine: nullable(wire.preferred_machine, toName),
    expectedDuration: textOrNull(wire.expected_duration),
    instructions: textOrNull(wire.instructions),
  };
}

function toRoute(value: unknown): ArchivedRoute {
  const wire = record(value);
  return {
    assignedRouteId: int(wire.assigned_route_id),
    sourceRouteTemplateId: intOrNull(wire.source_route_template_id),
    createdAt: textOrNull(wire.created_at),
    steps: list(wire.steps).map(toRouteStep),
  };
}

function toFlow(value: unknown): ArchivedFlow {
  const wire = record(value);
  return {
    id: int(wire.id),
    partNumber: text(wire.part_number),
    partNumberName: textOrNull(wire.part_number_name),
    componentId: int(wire.component_id),
    quantity: int(wire.quantity),
    status: text(wire.status),
    routeMode: text(wire.route_mode),
    createdAt: text(wire.created_at),
    closedAt: textOrNull(wire.closed_at),
    record: record(wire.record),
    stockedArea: nullable(wire.stocked_area, toName),
    scrappedArea: nullable(wire.scrapped_area, toName),
    demand: nullable(wire.demand, toDemand),
    workOrders: list(wire.work_orders).map(toWorkOrder),
    parents: list(wire.parents).map(toFlowLink),
    children: list(wire.children).map(toFlowLink),
    route: nullable(wire.route, toRoute),
    movements: list(wire.movements).map(toMovement),
  };
}

function toNames(value: unknown): ArchiveViewNames {
  const wire = record(value);
  return {
    areas: list(wire.areas).map(toName),
    operations: list(wire.operations).map(toOperation),
    machines: list(wire.machines).map(toName),
    workers: list(wire.workers).map(toName),
  };
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

const VIEWS_PATH = '/api/history-archive/views';

/** The newest views first (the server's default limit when none is given;
 * it accepts 1 to 500). */
export async function listArchiveViews(limit?: number): Promise<ArchiveView[]> {
  const query = limit === undefined ? '' : `?limit=${limit}`;
  return list(await apiRequest<unknown>(`${VIEWS_PATH}${query}`)).map(toView);
}

/** Open the archive of run `runId` for viewing: answers the new view
 * (`REQUESTED`); the server's archival job loads it within minutes. */
export async function openArchiveForViewing(
  runId: number,
): Promise<ArchiveView> {
  return toView(
    await apiRequest<unknown>(VIEWS_PATH, {
      method: 'POST',
      body: { run_id: runId },
    }),
  );
}

/** Cancel a view waiting to be loaded (a cancelled view answers
 * unchanged). */
export async function cancelArchiveView(viewId: number): Promise<ArchiveView> {
  return toView(
    await apiRequest<unknown>(`${VIEWS_PATH}/${viewId}/cancel`, {
      method: 'POST',
    }),
  );
}

/** Close viewing: an open view becomes `CLOSING` and the archival job
 * removes its loaded rows (a closing or closed view answers unchanged). */
export async function closeArchiveView(viewId: number): Promise<ArchiveView> {
  return toView(
    await apiRequest<unknown>(`${VIEWS_PATH}/${viewId}/close`, {
      method: 'POST',
    }),
  );
}

/** The wire name of each search term. */
const SEARCH_PARAMS: readonly [keyof ArchivedMovementSearch, string][] = [
  ['partNumber', 'part_number'],
  ['workOrderNumber', 'work_order_number'],
  ['quantityFlowId', 'quantity_flow_id'],
  ['areaId', 'area_id'],
  ['operationId', 'operation_id'],
  ['machineId', 'machine_id'],
  ['workerId', 'worker_id'],
  ['dateFrom', 'date_from'],
  ['dateTo', 'date_to'],
];

/** One page of an open view's Movements matching `search`, newest first;
 * `before` is the `nextBeforeMovementId` the previous page answered,
 * passed back unchanged (null for the first page). */
export async function searchArchivedMovements(
  viewId: number,
  search: ArchivedMovementSearch,
  before: number | null,
  limit: number,
): Promise<ArchivedMovementPage> {
  const params = new URLSearchParams();
  for (const [term, name] of SEARCH_PARAMS) {
    const value = search[term];
    if (value !== undefined) params.set(name, String(value));
  }
  if (before !== null) params.set('before', String(before));
  params.set('limit', String(limit));
  return toMovementPage(
    await apiRequest<unknown>(
      `${VIEWS_PATH}/${viewId}/movements?${params.toString()}`,
    ),
  );
}

/** One archived flow of an open view, with its whole history. */
export async function loadArchivedFlow(
  viewId: number,
  flowId: number,
): Promise<ArchivedFlow> {
  return toFlow(
    await apiRequest<unknown>(`${VIEWS_PATH}/${viewId}/flows/${flowId}`),
  );
}

/** The Areas, Operations, Machines and Workers an open view names. */
export async function loadArchiveViewNames(
  viewId: number,
): Promise<ArchiveViewNames> {
  return toNames(await apiRequest<unknown>(`${VIEWS_PATH}/${viewId}/names`));
}
