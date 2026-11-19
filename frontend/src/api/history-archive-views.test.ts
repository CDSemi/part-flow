import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  cancelArchiveView,
  closeArchiveView,
  listArchiveViews,
  loadArchivedFlow,
  loadArchiveViewNames,
  openArchiveForViewing,
  searchArchivedMovements,
} from './history-archive-views';

// The viewing API of history archival step 5 (archival design D16): the
// exact paths, query terms and bodies of the views and the viewer, the
// wire ↔ model mapping of a view, an archived Movement, a flow and the
// names — names the archive does not hold stay null — and a loud failure
// on a malformed answer instead of a wrong view.

let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function sent(): { path: string; method: string; body: unknown } {
  const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return {
    path,
    method: init.method ?? 'GET',
    body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
  };
}

const TO_LOAD = {
  archive_name: 'run-2-20261119T015409Z',
  movements: 2006882,
  flows: 153345,
  lineage_edges: 10010,
  assigned_routes: 1001,
  archive_bytes: 183827649,
};

function viewWire(extra: Record<string, unknown> = {}) {
  return {
    id: 4,
    run_id: 2,
    state: 'OPEN',
    requested_by: { user_id: 1, display_name: 'Ann' },
    requested_at: '2026-11-18T10:00:00.000000Z',
    to_load: TO_LOAD,
    started_at: '2026-11-18T10:05:00.000000Z',
    opened_at: '2026-11-18T10:05:33.000000Z',
    open_until: '2026-11-25T10:05:33.000000Z',
    loaded: {
      movements: 2006882,
      flows: 153345,
      lineage_edges: 10010,
      assigned_routes: 1001,
      names: 291775,
    },
    closing_started_at: null,
    closed_by: null,
    cancelled_by: null,
    finished_at: null,
    failure_reason: null,
    ...extra,
  };
}

function movementWire(extra: Record<string, unknown> = {}) {
  return {
    id: 812400,
    quantity_flow_id: 5512,
    part_number: 'AD-0001',
    movement_type: 'RECEIVED',
    quantity: 10,
    from_area: null,
    to_area: { id: 3, name: 'MAT' },
    operation: { id: 7, code: 'OP-MAT', name: null },
    source_machine: null,
    destination_machine: null,
    station_id: null,
    worker: null,
    occurred_at: '2023-11-18T02:06:00.000000Z',
    device_event_id: 'evt-1',
    command_sequence: 1,
    movement_reason: null,
    reason: null,
    reverses_movement_id: null,
    reversed_by_movement_id: null,
    assigned_route_step: { id: 41, sequence: 1 },
    route_deviation: null,
    lineage: [],
    demand: {
      work_order_demand_id: 90,
      work_order_id: 31,
      work_order_number: 'WO-1',
      request_type: 'NEW',
    },
    actor_user: { user_id: 1, display_name: 'Ann' },
    ...extra,
  };
}

function flowWire(extra: Record<string, unknown> = {}) {
  return {
    id: 5512,
    part_number: 'AD-0001',
    part_number_name: 'Bracket',
    component_id: 5510,
    quantity: 10,
    status: 'STOCKED',
    route_mode: 'PLANNED',
    created_at: '2023-11-01T08:00:00.000000Z',
    closed_at: '2023-11-18T02:06:00.000000Z',
    record: { flow_status: 'STOCKED', stocked_quantity: 10 },
    stocked_area: { id: 9, name: 'STOCK' },
    scrapped_area: null,
    demand: null,
    work_orders: [{ work_order_id: 31, work_order_number: 'WO-1' }],
    parents: [{ quantity_flow_id: 5510, relation: 'SPLIT', void: false }],
    children: [],
    route: {
      assigned_route_id: 12,
      source_route_template_id: 3,
      created_at: '2023-11-01T08:00:00.000000Z',
      steps: [
        {
          id: 41,
          sequence: 1,
          area: { id: 3, name: 'MAT' },
          operation: { id: 7, code: 'OP-MAT', name: null },
          preferred_machine: null,
          expected_duration: null,
          instructions: null,
        },
      ],
    },
    movements: [movementWire()],
    ...extra,
  };
}

test('the views list maps each view, with what its run exported and what it loaded', async () => {
  fetchMock.mockResolvedValue(
    json([
      viewWire(),
      viewWire({
        id: 3,
        run_id: 1,
        state: 'CLOSED',
        to_load: { ...TO_LOAD, archive_name: null },
        closing_started_at: '2026-11-17T09:00:00.000000Z',
        finished_at: '2026-11-17T09:00:09.000000Z',
      }),
    ]),
  );
  const [open, closed] = await listArchiveViews(500);
  expect(sent()).toEqual({
    path: '/api/history-archive/views?limit=500',
    method: 'GET',
    body: undefined,
  });
  expect(open).toEqual({
    id: 4,
    runId: 2,
    state: 'OPEN',
    requestedBy: { userId: 1, displayName: 'Ann' },
    requestedAt: '2026-11-18T10:00:00.000000Z',
    toLoad: {
      archiveName: 'run-2-20261119T015409Z',
      movements: 2006882,
      flows: 153345,
      lineageEdges: 10010,
      assignedRoutes: 1001,
      archiveBytes: 183827649,
    },
    startedAt: '2026-11-18T10:05:00.000000Z',
    openedAt: '2026-11-18T10:05:33.000000Z',
    openUntil: '2026-11-25T10:05:33.000000Z',
    loaded: {
      movements: 2006882,
      flows: 153345,
      lineageEdges: 10010,
      assignedRoutes: 1001,
      names: 291775,
    },
    closingStartedAt: null,
    closedBy: null,
    cancelledBy: null,
    finishedAt: null,
    failureReason: null,
  });
  // Its days ran out: nobody closed it.
  expect(closed).toMatchObject({
    state: 'CLOSED',
    closedBy: null,
    toLoad: { archiveName: null },
  });

  fetchMock.mockResolvedValue(json([]));
  await listArchiveViews();
  expect(sent().path).toBe('/api/history-archive/views');
});

test('open posts the run id; cancel and close post nothing to the view', async () => {
  fetchMock.mockResolvedValue(
    json(
      viewWire({
        state: 'REQUESTED',
        started_at: null,
        opened_at: null,
        open_until: null,
        loaded: null,
      }),
      201,
    ),
  );
  const requested = await openArchiveForViewing(2);
  expect(sent()).toEqual({
    path: '/api/history-archive/views',
    method: 'POST',
    body: { run_id: 2 },
  });
  expect(requested).toMatchObject({
    state: 'REQUESTED',
    loaded: null,
    openUntil: null,
  });

  fetchMock.mockResolvedValue(
    json(
      viewWire({
        state: 'CANCELLED',
        cancelled_by: { user_id: 5, display_name: 'Bo' },
        finished_at: '2026-11-18T10:01:00.000000Z',
      }),
    ),
  );
  const cancelled = await cancelArchiveView(4);
  expect(sent()).toEqual({
    path: '/api/history-archive/views/4/cancel',
    method: 'POST',
    body: undefined,
  });
  expect(cancelled.cancelledBy).toEqual({ userId: 5, displayName: 'Bo' });

  fetchMock.mockResolvedValue(
    json(
      viewWire({
        state: 'CLOSING',
        closing_started_at: '2026-11-19T08:00:00.000000Z',
        closed_by: { user_id: 1, display_name: 'Ann' },
      }),
    ),
  );
  const closing = await closeArchiveView(4);
  expect(sent()).toEqual({
    path: '/api/history-archive/views/4/close',
    method: 'POST',
    body: undefined,
  });
  expect(closing).toMatchObject({
    state: 'CLOSING',
    closedBy: { userId: 1, displayName: 'Ann' },
  });
});

test('a search sends only the terms given, the cursor and the page size, and maps the Movements', async () => {
  fetchMock.mockResolvedValue(
    json({
      movements: [
        movementWire(),
        movementWire({
          id: 812399,
          movement_type: 'SPLIT',
          to_area: { id: 3, name: null },
          operation: { id: 7, code: null, name: null },
          worker: { id: 8, name: 'Alex' },
          assigned_route_step: { id: 99, sequence: null },
          lineage: [
            {
              parent_flow_id: 5512,
              child_flow_id: 5513,
              relation: 'SPLIT',
              void: true,
            },
          ],
          demand: null,
          actor_user: { user_id: 6, display_name: null },
          route_deviation: { reason: 'Rework' },
        }),
      ],
      total: 4210,
      has_more: true,
      next_before_movement_id: 812399,
    }),
  );
  const page = await searchArchivedMovements(
    4,
    {
      partNumber: 'ad 0001',
      workOrderNumber: 'WO 1/2',
      quantityFlowId: 5512,
      areaId: 3,
      operationId: 7,
      machineId: 11,
      workerId: 8,
      dateFrom: '2023-01-01',
      dateTo: '2023-12-31',
    },
    812500,
    50,
  );
  const url = new URL(sent().path, 'http://x');
  expect(url.pathname).toBe('/api/history-archive/views/4/movements');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    part_number: 'ad 0001',
    work_order_number: 'WO 1/2',
    quantity_flow_id: '5512',
    area_id: '3',
    operation_id: '7',
    machine_id: '11',
    worker_id: '8',
    date_from: '2023-01-01',
    date_to: '2023-12-31',
    before: '812500',
    limit: '50',
  });
  expect(page).toMatchObject({
    total: 4210,
    hasMore: true,
    nextBeforeMovementId: 812399,
  });
  expect(page.movements[0]).toEqual({
    id: 812400,
    quantityFlowId: 5512,
    partNumber: 'AD-0001',
    movementType: 'RECEIVED',
    quantity: 10,
    fromArea: null,
    toArea: { id: 3, name: 'MAT' },
    operation: { id: 7, code: 'OP-MAT', name: null },
    sourceMachine: null,
    destinationMachine: null,
    stationId: null,
    worker: null,
    occurredAt: '2023-11-18T02:06:00.000000Z',
    deviceEventId: 'evt-1',
    commandSequence: 1,
    movementReason: null,
    reason: null,
    reversesMovementId: null,
    reversedByMovementId: null,
    assignedRouteStep: { id: 41, sequence: 1 },
    routeDeviation: null,
    lineage: [],
    demand: {
      workOrderDemandId: 90,
      workOrderId: 31,
      workOrderNumber: 'WO-1',
      requestType: 'NEW',
    },
    actorUser: { userId: 1, displayName: 'Ann' },
  });
  expect(page.movements[1]).toMatchObject({
    toArea: { id: 3, name: null },
    operation: { id: 7, code: null, name: null },
    worker: { id: 8, name: 'Alex' },
    assignedRouteStep: { id: 99, sequence: null },
    lineage: [
      { parentFlowId: 5512, childFlowId: 5513, relation: 'SPLIT', void: true },
    ],
    actorUser: { userId: 6, displayName: null },
    routeDeviation: { reason: 'Rework' },
  });

  // No term, the first page: only the page size.
  fetchMock.mockResolvedValue(
    json({
      movements: [],
      total: 0,
      has_more: false,
      next_before_movement_id: null,
    }),
  );
  await searchArchivedMovements(4, {}, null, 50);
  expect(sent().path).toBe('/api/history-archive/views/4/movements?limit=50');
});

test('a flow maps its record, Work Orders, lineage, route and history; the names map by kind', async () => {
  fetchMock.mockResolvedValue(json(flowWire()));
  const flow = await loadArchivedFlow(4, 5512);
  expect(sent().path).toBe('/api/history-archive/views/4/flows/5512');
  expect(flow).toMatchObject({
    id: 5512,
    partNumber: 'AD-0001',
    partNumberName: 'Bracket',
    componentId: 5510,
    status: 'STOCKED',
    routeMode: 'PLANNED',
    record: { flow_status: 'STOCKED', stocked_quantity: 10 },
    stockedArea: { id: 9, name: 'STOCK' },
    scrappedArea: null,
    demand: null,
    workOrders: [{ workOrderId: 31, workOrderNumber: 'WO-1' }],
    parents: [{ quantityFlowId: 5510, relation: 'SPLIT', void: false }],
    children: [],
    route: {
      assignedRouteId: 12,
      sourceRouteTemplateId: 3,
      steps: [
        {
          id: 41,
          sequence: 1,
          area: { id: 3, name: 'MAT' },
          operation: { id: 7, code: 'OP-MAT', name: null },
          preferredMachine: null,
        },
      ],
    },
  });
  expect(flow.movements.map((movement) => movement.id)).toEqual([812400]);

  fetchMock.mockResolvedValue(
    json({
      areas: [{ id: 3, name: 'MAT' }],
      operations: [{ id: 7, code: 'OP-MAT', name: null }],
      machines: [{ id: 11, name: null }],
      workers: [{ id: 8, name: 'Alex' }],
    }),
  );
  expect(await loadArchiveViewNames(4)).toEqual({
    areas: [{ id: 3, name: 'MAT' }],
    operations: [{ id: 7, code: 'OP-MAT', name: null }],
    machines: [{ id: 11, name: null }],
    workers: [{ id: 8, name: 'Alex' }],
  });
  expect(sent().path).toBe('/api/history-archive/views/4/names');
});

test('a malformed answer throws instead of showing a wrong view', async () => {
  const MALFORMED = 'The server answered malformed archive viewing data.';
  const { failure_reason: _omitted, ...withoutFailure } = viewWire();
  void _omitted;
  for (const wire of [
    viewWire({ state: 'LOADED' }),
    viewWire({ id: '4' }),
    viewWire({ requested_by: null }),
    viewWire({ to_load: { ...TO_LOAD, movements: -1 } }),
    viewWire({ loaded: { movements: 1 } }),
    viewWire({ open_until: 7 }),
    withoutFailure,
  ]) {
    fetchMock.mockResolvedValueOnce(json([wire]));
    await expect(listArchiveViews()).rejects.toThrow(MALFORMED);
  }
  for (const wire of [
    { movements: [movementWire({ to_area: null })], total: 1 },
    {
      movements: [movementWire({ quantity: 1.5 })],
      total: 1,
      has_more: false,
      next_before_movement_id: null,
    },
    {
      movements: [movementWire({ lineage: [{ parent_flow_id: 1 }] })],
      total: 1,
      has_more: false,
      next_before_movement_id: null,
    },
    {
      movements: [],
      total: -1,
      has_more: false,
      next_before_movement_id: null,
    },
  ]) {
    fetchMock.mockResolvedValueOnce(json(wire));
    await expect(searchArchivedMovements(4, {}, null, 50)).rejects.toThrow(
      MALFORMED,
    );
  }
  for (const wire of [
    flowWire({ record: [] }),
    flowWire({ parents: [{ quantity_flow_id: 1, relation: 'SPLIT' }] }),
    flowWire({ route: { assigned_route_id: 12, steps: [{ id: 1 }] } }),
  ]) {
    fetchMock.mockResolvedValueOnce(json(wire));
    await expect(loadArchivedFlow(4, 5512)).rejects.toThrow(MALFORMED);
  }
  fetchMock.mockResolvedValueOnce(json({ areas: [], operations: [] }));
  await expect(loadArchiveViewNames(4)).rejects.toThrow(MALFORMED);
});

test("a refusal keeps the server's text", async () => {
  const LIMIT =
    'At most 1 run may be loaded for viewing at once (History archival & purge), and 1 is loaded or waiting: view 3 of run 1 (OPEN). Close a view, or wait until it is closed, before opening run 2.';
  fetchMock.mockResolvedValue(json({ detail: LIMIT }, 409));
  await expect(openArchiveForViewing(2)).rejects.toMatchObject({
    status: 409,
    message: LIMIT,
  });
});
