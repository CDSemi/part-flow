import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ApiError } from './client';
import {
  cancelDeletionRequest,
  deletionRefusals,
  listDeletionRequests,
  previewDeletion,
  requestDeletion,
} from './permanent-deletion';

// The permanent deletion API (archival design D17, build step 3): exact
// paths and bodies, the wire ↔ model mapping of the preview and the
// request object, a loud failure on a malformed answer, and the refusals
// a 409 `deletion_refused` carries.

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

const ROWS = {
  audit_events: 4,
  part_movements: 12,
  quantity_flow_lineage: 0,
  quantity_flows: 2,
  assigned_route_steps: 6,
  assigned_routes: 2,
  work_order_allocations: 0,
  work_order_demands: 1,
  work_orders: 1,
  part_numbers: 0,
};

const WORK_ORDER_ENTRY = {
  id: 7,
  work_order_number: 'A-1',
  internal: false,
  named: true,
  part_numbers: ['PN-1'],
  demand_lines: 1,
};

function requestWire(extra: Record<string, unknown> = {}) {
  return {
    id: 5,
    state: 'APPROVED',
    scope: { work_order_numbers: ['A-1'], part_numbers: [] },
    reason: 'Test data entered by mistake',
    requested_by: { user_id: 9, display_name: 'Sam Super' },
    requested_by_operator: null,
    requested_at: '2026-11-18T10:00:00Z',
    work_orders: [WORK_ORDER_ENTRY],
    part_numbers: [],
    rows: ROWS,
    started_at: null,
    backup_label: null,
    pre_purge_backup: null,
    finished_at: null,
    cancelled_by: null,
    failure_reason: null,
    ...extra,
  };
}

function previewWire(extra: Record<string, unknown> = {}) {
  return {
    scope: { work_order_numbers: ['A-1'], part_numbers: [] },
    work_orders: [WORK_ORDER_ENTRY],
    part_numbers: [{ part_number: 'PN-1', master: true }],
    rows: ROWS,
    kept_audit_rows: [
      {
        id: 90,
        entity_type: 'WorkOrderDemand',
        entity_id: '5',
        event_type: 'UPDATED',
        occurred_at: '2026-11-17T08:00:00.000000Z',
        kept: 'also concerns other records',
      },
    ],
    refusals: [],
    open_request: null,
    ...extra,
  };
}

test('the preview asks by Work Order Number or Part Number and maps what goes', async () => {
  fetchMock.mockResolvedValue(json(previewWire()));
  const preview = await previewDeletion({
    kind: 'work-orders',
    workOrderNumbers: ['A 1/2', 'B&3'],
  });
  expect(sent()).toEqual({
    path: '/api/permanent-deletion/preview?work_order_number=A%201%2F2&work_order_number=B%263',
    method: 'GET',
    body: undefined,
  });
  expect(preview).toEqual({
    scope: { workOrderNumbers: ['A-1'], partNumbers: [] },
    workOrders: [
      {
        id: 7,
        workOrderNumber: 'A-1',
        internal: false,
        named: true,
        partNumbers: ['PN-1'],
        demandLines: 1,
      },
    ],
    partNumbers: [{ partNumber: 'PN-1', master: true }],
    rows: ROWS,
    keptAuditRows: [
      {
        id: 90,
        entityType: 'WorkOrderDemand',
        entityId: '5',
        eventType: 'UPDATED',
        occurredAt: '2026-11-17T08:00:00.000000Z',
        kept: 'also concerns other records',
      },
    ],
    refusals: [],
    openRequest: null,
  });

  fetchMock.mockResolvedValue(
    json(
      previewWire({
        scope: { work_order_numbers: [], part_numbers: ['PN-1'] },
        work_orders: [
          {
            ...WORK_ORDER_ENTRY,
            id: 8,
            work_order_number: null,
            internal: true,
            named: false,
          },
        ],
      }),
    ),
  );
  const byPart = await previewDeletion({
    kind: 'part-number',
    partNumber: 'pn 1',
  });
  expect(sent().path).toBe(
    '/api/permanent-deletion/preview?part_number=pn%201',
  );
  expect(byPart.workOrders[0]).toMatchObject({
    workOrderNumber: null,
    internal: true,
    named: false,
  });
});

test('a refused preview has no rows and every refusal; an open request maps in full', async () => {
  fetchMock.mockResolvedValue(
    json(
      previewWire({
        rows: null,
        kept_audit_rows: [],
        refusals: [
          { code: 'on_hot_list', message: 'Demand line 3 is on the Hot list.' },
          { code: 'lineage_link', message: 'Flow 4 is linked to flow 9.' },
        ],
        open_request: requestWire({
          state: 'RUNNING',
          requested_by: null,
          requested_by_operator: 'ops-anna',
          started_at: '2026-11-18T10:05:00Z',
          backup_label: 'deletion-5-20261118T100500Z',
        }),
      }),
    ),
  );
  const preview = await previewDeletion({
    kind: 'work-orders',
    workOrderNumbers: ['A-1'],
  });
  expect(preview.rows).toBeNull();
  expect(preview.refusals).toEqual([
    { code: 'on_hot_list', message: 'Demand line 3 is on the Hot list.' },
    { code: 'lineage_link', message: 'Flow 4 is linked to flow 9.' },
  ]);
  expect(preview.openRequest).toEqual({
    id: 5,
    state: 'RUNNING',
    scope: { workOrderNumbers: ['A-1'], partNumbers: [] },
    reason: 'Test data entered by mistake',
    requestedBy: null,
    requestedByOperator: 'ops-anna',
    requestedAt: '2026-11-18T10:00:00Z',
    workOrders: [
      {
        id: 7,
        workOrderNumber: 'A-1',
        internal: false,
        named: true,
        partNumbers: ['PN-1'],
        demandLines: 1,
      },
    ],
    partNumbers: [],
    rows: ROWS,
    startedAt: '2026-11-18T10:05:00Z',
    backupLabel: 'deletion-5-20261118T100500Z',
    prePurgeBackup: null,
    finishedAt: null,
    cancelledBy: null,
    failureReason: null,
  });
});

test('a request posts the scope, the reason and the confirmation as typed', async () => {
  fetchMock.mockResolvedValue(json(requestWire(), 201));
  const created = await requestDeletion(
    { kind: 'work-orders', workOrderNumbers: ['A-1'] },
    '  Test data entered by mistake ',
    ['a-1 '],
  );
  expect(sent()).toEqual({
    path: '/api/permanent-deletion/requests',
    method: 'POST',
    body: {
      work_order_numbers: ['A-1'],
      reason: '  Test data entered by mistake ',
      confirmation: ['a-1 '],
    },
  });
  expect(created).toMatchObject({
    id: 5,
    state: 'APPROVED',
    requestedBy: { userId: 9, displayName: 'Sam Super' },
  });

  fetchMock.mockResolvedValue(
    json(
      requestWire({
        scope: { work_order_numbers: [], part_numbers: ['PN-1'] },
      }),
      201,
    ),
  );
  await requestDeletion({ kind: 'part-number', partNumber: 'PN-1' }, 'x', [
    'pn-1',
  ]);
  expect(sent().body).toEqual({
    part_number: 'PN-1',
    reason: 'x',
    confirmation: ['pn-1'],
  });
});

test('cancel and list reach their routes; a finished request maps its backup, canceller and failure', async () => {
  fetchMock.mockResolvedValue(
    json(
      requestWire({
        state: 'CANCELLED',
        finished_at: '2026-11-18T10:02:00Z',
        cancelled_by: { user_id: 3, display_name: 'Ada Admin' },
      }),
    ),
  );
  const cancelled = await cancelDeletionRequest(5);
  expect(sent()).toEqual({
    path: '/api/permanent-deletion/requests/5/cancel',
    method: 'POST',
    body: undefined,
  });
  expect(cancelled.cancelledBy).toEqual({
    userId: 3,
    displayName: 'Ada Admin',
  });

  fetchMock.mockResolvedValue(
    json([
      requestWire({
        id: 6,
        state: 'COMPLETED',
        started_at: '2026-11-18T10:05:00Z',
        backup_label: 'deletion-6-20261118T100500Z',
        pre_purge_backup: {
          name: '20261118T100501Z-pre-purge-deletion-6-20261118T100500Z',
          dump_started_at: '2026-11-18T10:05:01Z',
        },
        finished_at: '2026-11-18T10:06:00Z',
      }),
      requestWire({
        state: 'FAILED',
        finished_at: '2026-11-18T10:01:00Z',
        failure_reason:
          'The data changed after the request; nothing was deleted. Request it again.',
      }),
    ]),
  );
  const listed = await listDeletionRequests(10);
  expect(sent().path).toBe('/api/permanent-deletion/requests?limit=10');
  expect(listed.map((request) => request.state)).toEqual([
    'COMPLETED',
    'FAILED',
  ]);
  expect(listed[0].prePurgeBackup).toEqual({
    name: '20261118T100501Z-pre-purge-deletion-6-20261118T100500Z',
    dumpStartedAt: '2026-11-18T10:05:01Z',
  });
  expect(listed[1].failureReason).toBe(
    'The data changed after the request; nothing was deleted. Request it again.',
  );

  fetchMock.mockResolvedValue(json([]));
  await listDeletionRequests();
  expect(sent().path).toBe('/api/permanent-deletion/requests');
});

test('a malformed answer throws instead of showing a wrong request', async () => {
  const malformedRequests: Record<string, unknown>[] = [
    requestWire({ state: 'PURGING' }),
    requestWire({ id: '5' }),
    requestWire({ rows: { work_orders: -1 } }),
    requestWire({ rows: { work_orders: 1.5 } }),
    requestWire({ requested_by: { user_id: 9 } }),
    requestWire({ work_orders: [{ ...WORK_ORDER_ENTRY, named: 'yes' }] }),
    requestWire({ pre_purge_backup: { name: 'x' } }),
  ];
  const { failure_reason: _omitted, ...withoutFailure } = requestWire();
  void _omitted;
  malformedRequests.push(withoutFailure);
  for (const wire of malformedRequests) {
    fetchMock.mockResolvedValueOnce(json([wire]));
    await expect(listDeletionRequests()).rejects.toThrow(
      'The server answered malformed permanent deletion data.',
    );
  }
  for (const wire of [
    previewWire({ refusals: [{ code: 'x' }] }),
    previewWire({ kept_audit_rows: [{ id: 1 }] }),
    previewWire({ open_request: { id: 5 } }),
    previewWire({ rows: [] }),
    [],
  ]) {
    fetchMock.mockResolvedValueOnce(json(wire));
    await expect(
      previewDeletion({ kind: 'part-number', partNumber: 'PN-1' }),
    ).rejects.toThrow('The server answered malformed permanent deletion data.');
  }
});

test('the refusals of a 409 deletion_refused, and nothing for any other failure', () => {
  const refused = new ApiError(409, 'This deletion is refused…', {
    detail: 'This deletion is refused…',
    deletion_refused: true,
    refusals: [{ code: 'on_hot_list', message: 'Demand line 3 is Hot.' }],
  });
  expect(deletionRefusals(refused)).toEqual([
    { code: 'on_hot_list', message: 'Demand line 3 is Hot.' },
  ]);
  expect(
    deletionRefusals(
      new ApiError(409, 'Deletion request 4 is still open (APPROVED).', {
        detail: 'Deletion request 4 is still open (APPROVED).',
      }),
    ),
  ).toBeNull();
  expect(
    deletionRefusals(
      new ApiError(422, 'x', { detail: 'x', deletion_refused: true }),
    ),
  ).toBeNull();
  expect(
    deletionRefusals(
      new ApiError(409, 'x', {
        detail: 'x',
        deletion_refused: true,
        refusals: [{ code: 1 }],
      }),
    ),
  ).toBeNull();
  expect(deletionRefusals(new TypeError('Failed to fetch'))).toBeNull();
});
