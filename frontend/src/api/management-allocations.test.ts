import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  allocateBeyondDemand,
  allocateFromStock,
  getAllocationContext,
  reverseAllocation,
  toAllocationUserRef,
} from './management-allocations';
import { loadTrackingAllocations } from './tracking';

// Management allocation API (Phase 14 slice 5): the exact wire contract
// of the context read and the three Management commands — one demand
// line per command, the note omitted when there is none, the reversal
// body exactly `{reason, device_event_id}` — `created` from 201 / 200,
// the strict `actor_user` converter, and never a Scan Station header.

let fetchMock: ReturnType<typeof vi.fn>;
let answer: { body: unknown; status: number };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  answer = { body: { detail: 'Not answered here.' }, status: 500 };
  fetchMock = vi.fn(async () => json(answer.body, answer.status));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(): {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
} {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return {
    url,
    method: init.method ?? 'GET',
    headers: init.headers as Record<string, string>,
    body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
  };
}

const RESULT_WIRE = {
  kind: 'ALLOCATE',
  part_number: 'A-100',
  allocation_quantity: 4,
  rows: [],
  completed_work_order_ids: [1],
  reopened_work_order_ids: [],
  device_event_id: 'evt-1',
};

const MIA = { id: 90, display_name: 'Mia Manager', avatar_updated_at: null };

test('FC-1: the context read asks for a PN or one demand line and converts every field', async () => {
  answer = {
    status: 200,
    body: {
      part_number: 'A-100',
      stocked_quantity: 12,
      active_allocated_quantity: 10,
      available_stocked_quantity: 2,
      lines: [
        {
          work_order_id: 1,
          work_order_number: null,
          work_order_completed: true,
          received_date: '2026-08-01',
          work_order_demand_id: 7,
          request_type: 'MODIFY',
          due_date: null,
          priority_rank: 2,
          requested_quantity: 10,
          allocated_quantity: 12,
          remaining_shortage: 0,
          beyond_demand_quantity: 2,
          active_allocations: [
            {
              allocation_id: 31,
              quantity: 10,
              source: 'STOCKROOM',
              is_manual_override: false,
              exceeds_demand: false,
              allocation_reason: null,
              station_id: 'STOCK-1',
              allocated_at: '2026-08-02T08:00:00Z',
              actor_user: null,
            },
            {
              allocation_id: 32,
              quantity: 2,
              source: 'MANAGEMENT',
              is_manual_override: true,
              exceeds_demand: true,
              allocation_reason: 'customer accepted overage',
              station_id: null,
              allocated_at: '2026-08-03T08:00:00Z',
              actor_user: MIA,
            },
          ],
        },
      ],
    },
  };
  const context = await getAllocationContext({ workOrderDemandId: 7 });
  expect(lastCall().url).toBe(
    '/api/allocations/management/context?work_order_demand_id=7',
  );
  expect(context).toEqual({
    partNumber: 'A-100',
    stockedQuantity: 12,
    activeAllocatedQuantity: 10,
    availableStockedQuantity: 2,
    lines: [
      {
        workOrderId: 1,
        workOrderNumber: null,
        workOrderCompleted: true,
        receivedDate: '2026-08-01',
        workOrderDemandId: 7,
        requestType: 'MODIFY',
        dueDate: null,
        priorityRank: 2,
        requestedQuantity: 10,
        allocatedQuantity: 12,
        remainingShortage: 0,
        beyondDemandQuantity: 2,
        activeAllocations: [
          {
            allocationId: 31,
            quantity: 10,
            source: 'STOCKROOM',
            isManualOverride: false,
            exceedsDemand: false,
            allocationReason: null,
            stationId: 'STOCK-1',
            allocatedAt: '2026-08-02T08:00:00Z',
            actorUser: null,
          },
          {
            allocationId: 32,
            quantity: 2,
            source: 'MANAGEMENT',
            isManualOverride: true,
            exceedsDemand: true,
            allocationReason: 'customer accepted overage',
            stationId: null,
            allocatedAt: '2026-08-03T08:00:00Z',
            actorUser: {
              id: 90,
              displayName: 'Mia Manager',
              avatarUpdatedAt: null,
            },
          },
        ],
      },
    ],
  });

  answer = {
    status: 200,
    body: {
      part_number: 'A 1',
      stocked_quantity: 0,
      active_allocated_quantity: 0,
      available_stocked_quantity: 0,
      lines: [],
    },
  };
  await getAllocationContext({ partNumber: 'A 1' });
  expect(lastCall().url).toBe(
    '/api/allocations/management/context?part_number=A+1',
  );
});

test('FC-1: Allocate from stock posts one line of exactly the quantity; the note travels only when there is one', async () => {
  answer = { status: 201, body: RESULT_WIRE };
  const result = await allocateFromStock({
    partNumber: 'A-100',
    workOrderDemandId: 7,
    quantity: 4,
    note: null,
    deviceEventId: 'evt-1',
  });
  expect(lastCall()).toMatchObject({
    url: '/api/allocations/management',
    method: 'POST',
    body: {
      part_number: 'A-100',
      allocation_quantity: 4,
      lines: [{ work_order_demand_id: 7, quantity: 4 }],
      device_event_id: 'evt-1',
    },
  });
  expect(lastCall().body).not.toHaveProperty('reason');
  expect(result).toEqual({
    kind: 'ALLOCATE',
    partNumber: 'A-100',
    allocationQuantity: 4,
    completedWorkOrderIds: [1],
    reopenedWorkOrderIds: [],
    deviceEventId: 'evt-1',
    created: true,
  });

  answer = { status: 200, body: RESULT_WIRE };
  const replay = await allocateFromStock({
    partNumber: 'A-100',
    workOrderDemandId: 7,
    quantity: 4,
    note: 'left for later',
    deviceEventId: 'evt-1',
  });
  expect((lastCall().body as Record<string, unknown>).reason).toBe(
    'left for later',
  );
  expect(replay.created).toBe(false);
});

test('FC-1: the beyond-demand correction posts its own command', async () => {
  answer = {
    status: 201,
    body: { ...RESULT_WIRE, kind: 'ALLOCATE_BEYOND_DEMAND' },
  };
  const result = await allocateBeyondDemand({
    partNumber: 'A-100',
    workOrderDemandId: 7,
    quantity: 2,
    reason: 'customer accepted overage',
    deviceEventId: 'evt-2',
  });
  const call = lastCall();
  expect(call.url).toBe('/api/allocations/corrections');
  expect(call.method).toBe('POST');
  expect(call.body).toEqual({
    part_number: 'A-100',
    work_order_demand_id: 7,
    quantity: 2,
    reason: 'customer accepted overage',
    device_event_id: 'evt-2',
  });
  expect(result.kind).toBe('ALLOCATE_BEYOND_DEMAND');
});

test('FC-1: a reversal posts exactly the reason and the key — never a station', async () => {
  answer = {
    status: 201,
    body: {
      ...RESULT_WIRE,
      kind: 'REVERSE_ALLOCATION',
      completed_work_order_ids: [],
      reopened_work_order_ids: [1],
    },
  };
  const result = await reverseAllocation({
    allocationId: 31,
    reason: 'counted twice',
    deviceEventId: 'evt-3',
  });
  const call = lastCall();
  expect(call.url).toBe('/api/allocations/31/reversals');
  expect(call.body).toEqual({
    reason: 'counted twice',
    device_event_id: 'evt-3',
  });
  expect(result.reopenedWorkOrderIds).toEqual([1]);
});

test('FC-1: an unknown command kind is never reported as another one', async () => {
  answer = { status: 201, body: { ...RESULT_WIRE, kind: 'SOMETHING_ELSE' } };
  await expect(
    reverseAllocation({ allocationId: 31, reason: 'x', deviceEventId: 'e' }),
  ).rejects.toThrow();
});

test('FC-1: the actor reference is null, a User, or refused when malformed', () => {
  expect(toAllocationUserRef(null)).toBeNull();
  expect(toAllocationUserRef(undefined)).toBeNull();
  expect(
    toAllocationUserRef({
      id: 4,
      display_name: 'Lan',
      avatar_updated_at: '2026-08-01T00:00:00Z',
    }),
  ).toEqual({
    id: 4,
    displayName: 'Lan',
    avatarUpdatedAt: '2026-08-01T00:00:00Z',
  });
  expect(() => toAllocationUserRef('Lan')).toThrow();
  expect(() => toAllocationUserRef({ id: '4', display_name: 'Lan' })).toThrow();
  expect(() =>
    toAllocationUserRef({ id: 4, display_name: 'Lan', avatar_updated_at: 3 }),
  ).toThrow();
});

test('FC-1: the Tracking allocation history carries the beyond-demand flag and the actor', async () => {
  answer = {
    status: 200,
    body: {
      allocations: [
        {
          id: 32,
          quantity: 2,
          work_order: {
            work_order_id: 1,
            work_order_number: '007201',
            work_order_demand_id: 7,
            request_type: 'NEW',
          },
          source: 'MANAGEMENT',
          is_manual_override: true,
          allocation_reason: 'overage',
          reverses_allocation_id: null,
          reversed_by_allocation_id: null,
          station_id: null,
          allocated_at: '2026-08-03T08:00:00Z',
          exceeds_demand: true,
          actor_user: MIA,
        },
      ],
      total: 1,
      has_more: false,
      next_before_allocation_id: null,
    },
  };
  const page = await loadTrackingAllocations('A-100', 40, 100);
  expect(page.allocations[0].exceedsDemand).toBe(true);
  expect(page.allocations[0].actorUser).toEqual({
    id: 90,
    displayName: 'Mia Manager',
    avatarUpdatedAt: null,
  });
});

test('FC-10: Management allocation never sends a Scan Station device header', async () => {
  answer = { status: 201, body: RESULT_WIRE };
  await allocateFromStock({
    partNumber: 'A-100',
    workOrderDemandId: 7,
    quantity: 4,
    note: null,
    deviceEventId: 'evt-1',
  });
  await reverseAllocation({
    allocationId: 31,
    reason: 'x',
    deviceEventId: 'evt-4',
  });
  for (const [, init] of fetchMock.mock.calls as [string, RequestInit][]) {
    expect(Object.keys(init.headers as Record<string, string>)).not.toContain(
      'X-PartFlow-Station-Device',
    );
  }
  const source = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'management-allocations.ts'),
    'utf8',
  );
  expect(source).not.toMatch(/station-devices/);
  expect(source).not.toMatch(/stationDeviceHeaders/);
});
