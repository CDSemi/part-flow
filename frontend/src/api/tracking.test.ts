import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { loadTrackingDetail, loadTrackingMovements } from './tracking';

// The Tracking wire contract of archived history (Phase 16, history
// archival step 2): the `archived` entries and `archived_total` of every
// Movement page (the Movement history, the Scrap history and their
// continuation), the `archived` entry of a flow with its `ARCHIVED` route
// steps, and the continuation cursor passed back unchanged. A missing or
// malformed archived entry throws instead of hiding archived history.

const PN = '2027-60-8114-00';
const LATHE = { id: 2, name: 'Lathe', color: '#1565c0', is_terminal: false };
const SIZES = { movements: 50, flows: 50, allocations: 100, scrap: 20 };

let answer: () => Response;
let requests: string[];

beforeEach(() => {
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      requests.push(String(input));
      return answer();
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function archivedWire(extra: Record<string, unknown> = {}) {
  return {
    quantity_flow_id: 120,
    run_id: 3,
    archive_name: 'run-3-20271110T042000Z',
    flow_status: 'STOCKED',
    movement_count: 12,
    first_occurred_at: '2025-01-03T10:00:00Z',
    last_occurred_at: '2025-03-16T10:00:00.250000Z',
    last_movement_id: 77,
    purged_at: '2027-11-10T05:10:00Z',
    ...extra,
  };
}

const ARCHIVED = {
  quantityFlowId: 120,
  runId: 3,
  archiveName: 'run-3-20271110T042000Z',
  flowStatus: 'STOCKED',
  movementCount: 12,
  firstOccurredAt: '2025-01-03T10:00:00Z',
  lastOccurredAt: '2025-03-16T10:00:00.250000Z',
  lastMovementId: 77,
  purgedAt: '2027-11-10T05:10:00Z',
};

function movementPageWire(extra: Record<string, unknown> = {}) {
  return {
    movements: [],
    total: 0,
    has_more: false,
    next_before_movement_id: null,
    archived: [archivedWire()],
    archived_total: 1,
    ...extra,
  };
}

/** An archived Planned flow: its row, status and snapshot stay; its trace
 * and deviations are empty and every step reads ARCHIVED. */
function archivedFlowWire(extra: Record<string, unknown> = {}) {
  return {
    id: 120,
    quantity: 6,
    status: 'STOCKED',
    route_mode: 'PLANNED',
    created_at: '2025-01-03T10:00:00Z',
    closed_at: '2025-03-16T10:00:00Z',
    position: null,
    parents: [],
    children: [],
    trace: [],
    route_steps: [1, 2].map((id) => ({
      id,
      sequence: id,
      area: LATHE,
      operation: null,
      expected_duration: null,
      state: 'ARCHIVED',
    })),
    source_template: null,
    off_route: false,
    deviations: [],
    route_adjustments: [],
    archived: archivedWire(),
    ...extra,
  };
}

function detailWire(extra: Record<string, unknown> = {}) {
  return {
    part_number: PN,
    master: null,
    barcode_value: `PF:PN:${PN}`,
    status: 'COMPLETED',
    demands: [],
    locations: [],
    stocked: [],
    active_quantity: 0,
    stocked_quantity: 6,
    allocated_quantity: 0,
    available_stocked_quantity: 6,
    scrapped_quantity: 0,
    introduced_quantity: 6,
    flows: {
      flows: [archivedFlowWire()],
      total: 1,
      has_more: false,
      next_before_flow_id: null,
    },
    allocations: {
      allocations: [],
      total: 0,
      has_more: false,
      next_before_allocation_id: null,
    },
    movements: movementPageWire(),
    scrap_history: movementPageWire({ archived: [], archived_total: 0 }),
    route_adjustment_total: 0,
    ...extra,
  };
}

test('the detail carries the archived entries of its histories and the archived flow', async () => {
  answer = () => json(detailWire());
  const detail = await loadTrackingDetail(PN, SIZES);
  expect(detail.movements.archived).toEqual([ARCHIVED]);
  expect(detail.movements.archivedTotal).toBe(1);
  expect(detail.movements.total).toBe(0);
  expect(detail.scrapHistory.archived).toEqual([]);
  expect(detail.scrapHistory.archivedTotal).toBe(0);
  const [flow] = detail.flows.flows;
  expect(flow.archived).toEqual(ARCHIVED);
  expect(flow.trace).toEqual([]);
  expect(flow.routeSteps.map((step) => step.state)).toEqual([
    'ARCHIVED',
    'ARCHIVED',
  ]);
});

test('a flow whose history is kept has no archived entry; an entry may lack its archive name', async () => {
  answer = () =>
    json(
      detailWire({
        flows: {
          flows: [archivedFlowWire({ archived: null })],
          total: 1,
          has_more: false,
          next_before_flow_id: null,
        },
        movements: movementPageWire({
          archived: [archivedWire({ archive_name: null })],
        }),
      }),
    );
  const detail = await loadTrackingDetail(PN, SIZES);
  expect(detail.flows.flows[0].archived).toBeNull();
  expect(detail.movements.archived[0].archiveName).toBeNull();
});

test('a continuation passes the cursor back unchanged — an archived entry included — and reads its archived entries', async () => {
  answer = () =>
    json(
      movementPageWire({
        has_more: true,
        next_before_movement_id: 77,
        archived_total: 4,
      }),
    );
  const page = await loadTrackingMovements(PN, 77, 50, 'SCRAPPED');
  expect(requests).toEqual([
    `/api/tracking/movements?part_number=${PN}&before=77&limit=50&movement_type=SCRAPPED`,
  ]);
  expect(page).toEqual({
    movements: [],
    total: 0,
    hasMore: true,
    nextBeforeMovementId: 77,
    archived: [ARCHIVED],
    archivedTotal: 4,
  });
});

for (const [label, wire] of [
  [
    'a page without archived entries',
    movementPageWire({ archived: undefined }),
  ],
  [
    'a page without its archived total',
    movementPageWire({ archived_total: undefined }),
  ],
  ['a negative archived total', movementPageWire({ archived_total: -1 })],
  ['a fractional archived total', movementPageWire({ archived_total: 1.5 })],
  [
    'an entry without its run',
    movementPageWire({ archived: [archivedWire({ run_id: undefined })] }),
  ],
  [
    'an entry with a numeric archive name',
    movementPageWire({ archived: [archivedWire({ archive_name: 3 })] }),
  ],
  [
    'an entry without its newest Movement',
    movementPageWire({
      archived: [archivedWire({ last_movement_id: null })],
    }),
  ],
  ['an entry that is not an object', movementPageWire({ archived: [7] })],
] as const) {
  test(`a Movement page refuses ${label}`, async () => {
    answer = () => json(wire);
    await expect(loadTrackingMovements(PN, 5, 50)).rejects.toThrow(
      'The server answered a malformed archived history.',
    );
  });
}

test('the detail refuses a flow without its archived entry', async () => {
  answer = () =>
    json(
      detailWire({
        flows: {
          flows: [archivedFlowWire({ archived: undefined })],
          total: 1,
          has_more: false,
          next_before_flow_id: null,
        },
      }),
    );
  await expect(loadTrackingDetail(PN, SIZES)).rejects.toThrow(
    'The server answered a malformed archived history.',
  );
});
