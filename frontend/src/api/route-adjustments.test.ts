import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ApiError } from './client';
import {
  adjustAssignedRoute,
  isRouteChanged,
  loadAssignedRoutes,
} from './route-adjustments';
import { loadTrackingDetail, loadTrackingFlows } from './tracking';

// FE-10: the AssignedRoute adjustment wire contract (Phase 14 slice 6)
// — the editor read, the write and the Tracking additions (route notes
// on a flow block, the detail's route adjustment total). Converters
// throw on a missing or malformed field instead of rendering a wrong
// route; a missing actor is valid data.

const PN = '2027-60-8114-00';
const LATHE = { id: 2, name: 'Lathe', color: '#1565c0', is_terminal: false };
const TURN = { id: 21, code: 'TURN', name: 'Turning', is_external: false };

let answer: () => Response;
let requests: { url: string; init?: RequestInit }[];

beforeEach(() => {
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
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

function editorStepWire(extra: Record<string, unknown> = {}) {
  return {
    id: 3,
    sequence: 3,
    area: LATHE,
    operation: TURN,
    expected_duration: 'PT4H',
    preferred_machine: { id: 201, name: 'Lathe 1' },
    instructions: 'Check runout',
    state: 'FUTURE',
    locked: false,
    ...extra,
  };
}

function adjustableFlowWire(extra: Record<string, unknown> = {}) {
  return {
    quantity_flow_id: 140,
    quantity: 6,
    position: {
      area: LATHE,
      machine: null,
      operation: TURN,
      activity: null,
      state: 'QUEUE',
      since: '2030-07-22T11:20:00Z',
      expected_by: '2030-07-22T15:20:00Z',
    },
    off_route: false,
    source_template: { id: 7, name: 'Bracket std v3' },
    kept_through_sequence: 2,
    future_step_ids: [3],
    steps: [editorStepWire()],
    ...extra,
  };
}

test('FE-10: the editor read maps every field of the contract', async () => {
  answer = () => json({ part_number: PN, flows: [adjustableFlowWire()] });
  const flows = await loadAssignedRoutes(PN);
  expect(requests[0].url).toBe(
    `/api/tracking/assigned-routes?part_number=${PN}`,
  );
  expect(flows).toEqual([
    {
      quantityFlowId: 140,
      quantity: 6,
      position: {
        area: { id: 2, name: 'Lathe', color: '#1565c0', isTerminal: false },
        machine: null,
        operation: { id: 21, code: 'TURN', name: 'Turning', isExternal: false },
        activity: null,
        state: 'QUEUE',
        since: '2030-07-22T11:20:00Z',
        expectedBy: '2030-07-22T15:20:00Z',
      },
      offRoute: false,
      sourceTemplate: { id: 7, name: 'Bracket std v3' },
      keptThroughSequence: 2,
      futureStepIds: [3],
      steps: [
        {
          id: 3,
          sequence: 3,
          area: { id: 2, name: 'Lathe', color: '#1565c0', isTerminal: false },
          operation: {
            id: 21,
            code: 'TURN',
            name: 'Turning',
            isExternal: false,
          },
          expectedDuration: 'PT4H',
          preferredMachine: { id: 201, name: 'Lathe 1' },
          instructions: 'Check runout',
          state: 'FUTURE',
          locked: false,
        },
      ],
    },
  ]);
});

test('FE-10: a flow without a position, template, Operation or Machine is valid', async () => {
  answer = () =>
    json({
      part_number: PN,
      flows: [
        adjustableFlowWire({
          position: null,
          source_template: null,
          steps: [
            editorStepWire({
              operation: null,
              preferred_machine: null,
              expected_duration: null,
              instructions: null,
            }),
          ],
        }),
      ],
    });
  const [flow] = await loadAssignedRoutes(PN);
  expect(flow.position).toBeNull();
  expect(flow.sourceTemplate).toBeNull();
  expect(flow.steps[0]).toMatchObject({
    operation: null,
    preferredMachine: null,
    expectedDuration: null,
    instructions: null,
  });
});

for (const [label, flow] of [
  [
    'a step without `locked`',
    adjustableFlowWire({ steps: [editorStepWire({ locked: undefined })] }),
  ],
  [
    'an unknown step state',
    adjustableFlowWire({ steps: [editorStepWire({ state: 'SKIPPED' })] }),
  ],
  [
    'a missing future_step_ids',
    adjustableFlowWire({ future_step_ids: undefined }),
  ],
  [
    'a fractional kept-through sequence',
    adjustableFlowWire({ kept_through_sequence: 2.5 }),
  ],
  ['a malformed position', adjustableFlowWire({ position: { area: LATHE } })],
  [
    'a malformed Area',
    adjustableFlowWire({ steps: [editorStepWire({ area: { id: 2 } })] }),
  ],
] as const) {
  test(`FE-10: the editor read refuses ${label}`, async () => {
    answer = () => json({ part_number: PN, flows: [flow] });
    await expect(loadAssignedRoutes(PN)).rejects.toThrow(
      'The server answered a malformed assigned route.',
    );
  });
}

test('FE-10: the editor read refuses a body without flows', async () => {
  answer = () => json({ part_number: PN });
  await expect(loadAssignedRoutes(PN)).rejects.toThrow(/malformed/);
});

test('FE-10: the write posts exactly the contract body and maps the whole route', async () => {
  answer = () =>
    json(
      {
        device_event_id: 'evt-1',
        quantity_flow_id: 140,
        part_number: PN,
        assigned_route_id: 900,
        kept_through_sequence: 2,
        reason: 'Mill is down',
        steps: [
          {
            id: 1,
            sequence: 1,
            area_id: 1,
            operation_id: 11,
            expected_duration: null,
            preferred_machine_id: null,
            instructions: null,
          },
          {
            id: 77,
            sequence: 3,
            area_id: 5,
            operation_id: 51,
            expected_duration: 'PT2H',
            preferred_machine_id: 205,
            instructions: 'Deburr edges',
          },
        ],
      },
      201,
    );
  const result = await adjustAssignedRoute(140, {
    deviceEventId: 'evt-1',
    expectedFutureStepIds: [3, 4],
    steps: [
      {
        areaId: 5,
        operationId: 51,
        expectedDuration: 'PT2H',
        preferredMachineId: 205,
        instructions: 'Deburr edges',
      },
    ],
    reason: 'Mill is down',
  });
  expect(requests[0].url).toBe('/api/quantity-flows/140/route-adjustments');
  expect(requests[0].init?.method).toBe('POST');
  expect(JSON.parse(String(requests[0].init?.body))).toEqual({
    device_event_id: 'evt-1',
    expected_future_step_ids: [3, 4],
    steps: [
      {
        area_id: 5,
        operation_id: 51,
        expected_duration: 'PT2H',
        preferred_machine_id: 205,
        instructions: 'Deburr edges',
      },
    ],
    reason: 'Mill is down',
  });
  expect(result).toEqual({
    deviceEventId: 'evt-1',
    quantityFlowId: 140,
    partNumber: PN,
    assignedRouteId: 900,
    keptThroughSequence: 2,
    reason: 'Mill is down',
    steps: [
      {
        id: 1,
        sequence: 1,
        areaId: 1,
        operationId: 11,
        expectedDuration: null,
        preferredMachineId: null,
        instructions: null,
      },
      {
        id: 77,
        sequence: 3,
        areaId: 5,
        operationId: 51,
        expectedDuration: 'PT2H',
        preferredMachineId: 205,
        instructions: 'Deburr edges',
      },
    ],
  });
});

test('FE-10: a malformed write answer throws', async () => {
  answer = () =>
    json(
      {
        device_event_id: 'evt-1',
        quantity_flow_id: 140,
        part_number: PN,
        assigned_route_id: 900,
        kept_through_sequence: 2,
        reason: 'Mill is down',
      },
      201,
    );
  await expect(
    adjustAssignedRoute(140, {
      deviceEventId: 'evt-1',
      expectedFutureStepIds: [],
      steps: [],
      reason: 'Mill is down',
    }),
  ).rejects.toThrow(/malformed/);
});

test('isRouteChanged reads the route_changed refusal flag only', () => {
  expect(
    isRouteChanged(new ApiError(409, 'changed', { route_changed: true })),
  ).toBe(true);
  expect(isRouteChanged(new ApiError(409, 'other', { detail: 'x' }))).toBe(
    false,
  );
  expect(isRouteChanged(new TypeError('Failed to fetch'))).toBe(false);
});

// ---------------------------------------------------------------------------
// Tracking additions
// ---------------------------------------------------------------------------

function note(extra: Record<string, unknown> = {}) {
  return {
    audit_event_id: 501,
    occurred_at: '2030-07-22T12:00:00Z',
    reason: 'Mill is down',
    kept_through_sequence: 2,
    actor_user: {
      id: 90,
      display_name: 'Mia Manager',
      avatar_updated_at: null,
    },
    ...extra,
  };
}

function flowWire(extra: Record<string, unknown> = {}) {
  return {
    id: 140,
    quantity: 6,
    status: 'ACTIVE',
    route_mode: 'PLANNED',
    created_at: '2030-07-12T08:02:00Z',
    closed_at: null,
    position: null,
    parents: [],
    children: [],
    trace: [],
    route_steps: [],
    source_template: null,
    off_route: false,
    deviations: [],
    route_adjustments: [
      note(),
      note({ audit_event_id: 502, actor_user: null }),
    ],
    ...extra,
  };
}

function page(key: string) {
  return {
    [key]: [],
    total: 0,
    has_more: false,
    [`next_before_${key === 'movements' ? 'movement' : 'allocation'}_id`]: null,
  };
}

function detailWire(extra: Record<string, unknown> = {}) {
  return {
    part_number: PN,
    master: null,
    barcode_value: `PF:PN:${PN}`,
    status: 'ACTIVE',
    demands: [],
    locations: [],
    stocked: [],
    active_quantity: 6,
    stocked_quantity: 0,
    allocated_quantity: 0,
    available_stocked_quantity: 0,
    scrapped_quantity: 0,
    introduced_quantity: 6,
    flows: {
      flows: [flowWire()],
      total: 1,
      has_more: false,
      next_before_flow_id: null,
    },
    allocations: page('allocations'),
    movements: page('movements'),
    scrap_history: page('movements'),
    route_adjustment_total: 2,
    ...extra,
  };
}

const SIZES = { movements: 50, flows: 50, allocations: 100, scrap: 20 };

test('FE-10: a Tracking flow carries its route notes in payload order, with or without an actor', async () => {
  answer = () => json(detailWire());
  const detail = await loadTrackingDetail(PN, SIZES);
  expect(detail.routeAdjustmentTotal).toBe(2);
  expect(detail.flows.flows[0].routeAdjustments).toEqual([
    {
      auditEventId: 501,
      occurredAt: '2030-07-22T12:00:00Z',
      reason: 'Mill is down',
      keptThroughSequence: 2,
      actorUser: { id: 90, displayName: 'Mia Manager', avatarUpdatedAt: null },
    },
    {
      auditEventId: 502,
      occurredAt: '2030-07-22T12:00:00Z',
      reason: 'Mill is down',
      keptThroughSequence: 2,
      actorUser: null,
    },
  ]);
});

for (const [label, wire] of [
  [
    'a flow without route_adjustments',
    detailWire({
      flows: {
        flows: [flowWire({ route_adjustments: undefined })],
        total: 1,
        has_more: false,
        next_before_flow_id: null,
      },
    }),
  ],
  [
    'a note without its reason',
    detailWire({
      flows: {
        flows: [flowWire({ route_adjustments: [note({ reason: null })] })],
        total: 1,
        has_more: false,
        next_before_flow_id: null,
      },
    }),
  ],
  [
    'a note with a malformed actor',
    detailWire({
      flows: {
        flows: [
          flowWire({ route_adjustments: [note({ actor_user: { id: 90 } })] }),
        ],
        total: 1,
        has_more: false,
        next_before_flow_id: null,
      },
    }),
  ],
  [
    'a detail without route_adjustment_total',
    detailWire({ route_adjustment_total: undefined }),
  ],
] as const) {
  test(`FE-10: the Tracking detail refuses ${label}`, async () => {
    answer = () => json(wire);
    await expect(loadTrackingDetail(PN, SIZES)).rejects.toThrow(/malformed/);
  });
}

test('FE-10: an older flow page refuses a flow without route_adjustments', async () => {
  answer = () =>
    json({
      flows: [flowWire({ route_adjustments: undefined })],
      total: 1,
      has_more: false,
      next_before_flow_id: null,
    });
  await expect(loadTrackingFlows(PN, 141, 50)).rejects.toThrow(/malformed/);
});
