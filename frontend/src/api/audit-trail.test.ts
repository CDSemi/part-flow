import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { loadAuditTrail } from './audit-trail';
import type { AuditTrailField, AuditTrailKind } from './audit-trail';

// FT-1: the PN audit trail wire contract (Phase 14 slice 7) — the
// request parameters, the full conversion of every kind and payload
// (wire field names mapped to their camelCase identifiers, route steps
// with the ISO duration kept as sent, the recording User through the
// shared actor converter) and the refusal of a malformed answer.

const PN = '2027-60-8114-00';
const LATHE = { id: 2, name: 'Lathe', color: '#1565c0', is_terminal: false };
const OP10 = { id: 21, code: 'OP10', name: null, is_external: false };

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

const SUBJECT_NONE = {
  work_order_id: null,
  work_order_number: null,
  work_order_demand_id: null,
  demand_exists: null,
  quantity_flow_id: null,
};

function entryWire(extra: Record<string, unknown> = {}) {
  return {
    source: 'AUDIT',
    id: 1,
    occurred_at: '2030-07-23T08:00:00Z',
    kind: 'PART_NUMBER_UPDATED',
    actor_user: null,
    legacy_actor: null,
    reason: null,
    subject: SUBJECT_NONE,
    changes: [],
    priority: null,
    completion_trigger: null,
    allocation: null,
    route: null,
    ...extra,
  };
}

function pageWire(entries: unknown[], extra: Record<string, unknown> = {}) {
  return {
    part_number: PN,
    entries,
    total: entries.length,
    has_more: false,
    next_before_source: null,
    next_before_id: null,
    ...extra,
  };
}

const KINDS: AuditTrailKind[] = [
  'PART_NUMBER_CREATED',
  'PART_NUMBER_UPDATED',
  'PART_NUMBER_IMAGE_CHANGED',
  'PART_NUMBER_DELETED',
  'WORK_ORDER_CREATED',
  'WORK_ORDER_UPDATED',
  'WORK_ORDER_COMPLETED',
  'DEMAND_CREATED',
  'DEMAND_UPDATED',
  'PRIORITY_CHANGED',
  'ROUTE_ADJUSTED',
  'ALLOCATED',
  'ALLOCATION_REVERSED',
  'ALLOCATED_BEYOND_DEMAND',
  'CHANGE_RECORDED',
];

const WIRE_FIELDS: [string, AuditTrailField][] = [
  ['name', 'name'],
  ['current_revision', 'currentRevision'],
  ['erp_id', 'erpId'],
  ['image', 'image'],
  ['work_order_number', 'workOrderNumber'],
  ['received_date', 'receivedDate'],
  ['due_date', 'dueDate'],
  ['status', 'status'],
  ['request_type', 'requestType'],
  ['requested_quantity', 'requestedQuantity'],
  ['job_numbers', 'jobNumbers'],
  ['requester', 'requester'],
  ['reason', 'reason'],
  ['notes', 'notes'],
  ['priority_rank', 'priorityRank'],
];

test('the first page reads the PN only; a continuation sends the cursor pair and the limit', async () => {
  answer = () => json(pageWire([]));
  await loadAuditTrail(PN);
  await loadAuditTrail(PN, { source: 'ALLOCATION', id: 31 }, 20);
  expect(requests[0]).toBe(
    '/api/tracking/audit-trail?part_number=2027-60-8114-00',
  );
  const next = new URL(requests[1], 'http://partflow.test');
  expect(next.pathname).toBe('/api/tracking/audit-trail');
  expect(Object.fromEntries(next.searchParams)).toEqual({
    part_number: PN,
    before_source: 'ALLOCATION',
    before_id: '31',
    limit: '20',
  });
});

test('every kind converts, with the page figures and the next cursor', async () => {
  answer = () =>
    json(
      pageWire(
        KINDS.map((kind, index) => entryWire({ id: index + 1, kind })),
        {
          total: 40,
          has_more: true,
          next_before_source: 'AUDIT',
          next_before_id: 15,
        },
      ),
    );
  const page = await loadAuditTrail(PN);
  expect(page.partNumber).toBe(PN);
  expect(page.entries.map((entry) => entry.kind)).toEqual(KINDS);
  expect(page.total).toBe(40);
  expect(page.hasMore).toBe(true);
  expect(page.next).toEqual({ source: 'AUDIT', id: 15 });
});

test('every wire field name maps to its camelCase identifier; values stay as stored', async () => {
  answer = () =>
    json(
      pageWire([
        entryWire({
          changes: WIRE_FIELDS.map(([field], index) => ({
            field,
            before: index % 2 === 0 ? null : 'x',
            after: field === 'job_numbers' ? ['18112', '18113'] : index,
          })),
        }),
      ]),
    );
  const [entry] = (await loadAuditTrail(PN)).entries;
  expect(entry.changes.map((change) => change.field)).toEqual(
    WIRE_FIELDS.map(([, field]) => field),
  );
  expect(entry.changes[10]).toEqual({
    field: 'jobNumbers',
    before: null,
    after: ['18112', '18113'],
  });
  expect(entry.changes[14]).toEqual({
    field: 'priorityRank',
    before: null,
    after: 14,
  });
});

test('the payloads convert: completion, priority, allocation, route, actor and subject', async () => {
  answer = () =>
    json(
      pageWire([
        entryWire({
          id: 9,
          kind: 'WORK_ORDER_COMPLETED',
          completion_trigger: 'WORK_ORDER_SAVE',
          subject: {
            ...SUBJECT_NONE,
            work_order_id: 4,
            work_order_number: '007001',
          },
          actor_user: {
            id: 90,
            display_name: 'Mia Manager',
            avatar_updated_at: '2030-07-01T00:00:00Z',
          },
        }),
        entryWire({
          id: 8,
          kind: 'PRIORITY_CHANGED',
          legacy_actor: 'legacy-x',
          subject: {
            work_order_id: 4,
            work_order_number: null,
            work_order_demand_id: 40,
            demand_exists: false,
            quantity_flow_id: null,
          },
          changes: [{ field: 'priority_rank', before: 2, after: null }],
          priority: {
            action: 'AUTO_REMOVE',
            trigger: 'ALLOCATION',
            removal_reason: 'FULLY_ALLOCATED',
            shifted: false,
          },
        }),
        entryWire({
          source: 'ALLOCATION',
          id: 31,
          kind: 'ALLOCATED_BEYOND_DEMAND',
          reason: 'customer accepted overage',
          allocation: {
            quantity: 2,
            source: 'MANAGEMENT',
            is_manual_override: true,
            exceeds_demand: true,
            reverses_allocation_id: null,
            station_id: null,
          },
        }),
        entryWire({
          id: 7,
          kind: 'ROUTE_ADJUSTED',
          reason: 'Lathe 2 down',
          subject: { ...SUBJECT_NONE, quantity_flow_id: 140 },
          route: {
            kept_through_sequence: 2,
            before_steps: [
              {
                sequence: 3,
                area: LATHE,
                operation: OP10,
                expected_duration: 'PT45M',
                preferred_machine: { id: 201, name: 'Lathe 1' },
                instructions: 'Check runout',
              },
            ],
            after_steps: [
              {
                sequence: 3,
                area: LATHE,
                operation: null,
                expected_duration: 'PT1M30.5S',
                preferred_machine: null,
                instructions: null,
              },
            ],
          },
        }),
      ]),
    );
  const [completed, priority, beyond, route] = (await loadAuditTrail(PN))
    .entries;

  expect(completed.completionTrigger).toBe('WORK_ORDER_SAVE');
  expect(completed.subject).toEqual({
    workOrderId: 4,
    workOrderNumber: '007001',
    workOrderDemandId: null,
    demandExists: null,
    quantityFlowId: null,
  });
  expect(completed.actorUser).toEqual({
    id: 90,
    displayName: 'Mia Manager',
    avatarUpdatedAt: '2030-07-01T00:00:00Z',
  });

  expect(priority.actorUser).toBeNull();
  expect(priority.legacyActor).toBe('legacy-x');
  expect(priority.subject.demandExists).toBe(false);
  expect(priority.priority).toEqual({
    action: 'AUTO_REMOVE',
    trigger: 'ALLOCATION',
    removalReason: 'FULLY_ALLOCATED',
    shifted: false,
  });
  expect(priority.changes).toEqual([
    { field: 'priorityRank', before: 2, after: null },
  ]);

  expect(beyond.source).toBe('ALLOCATION');
  expect(beyond.reason).toBe('customer accepted overage');
  expect(beyond.allocation).toEqual({
    quantity: 2,
    source: 'MANAGEMENT',
    isManualOverride: true,
    exceedsDemand: true,
    reversesAllocationId: null,
    stationId: null,
  });

  expect(route.subject.quantityFlowId).toBe(140);
  expect(route.route).toEqual({
    keptThroughSequence: 2,
    beforeSteps: [
      {
        sequence: 3,
        area: { id: 2, name: 'Lathe', color: '#1565c0', isTerminal: false },
        operation: { id: 21, code: 'OP10', name: null, isExternal: false },
        expectedDuration: 'PT45M',
        preferredMachine: { id: 201, name: 'Lathe 1' },
        instructions: 'Check runout',
      },
    ],
    afterSteps: [
      {
        sequence: 3,
        area: { id: 2, name: 'Lathe', color: '#1565c0', isTerminal: false },
        operation: null,
        expectedDuration: 'PT1M30.5S',
        preferredMachine: null,
        instructions: null,
      },
    ],
  });
});

test.each([
  ['an unknown source', entryWire({ source: 'MOVEMENT' })],
  ['an unknown kind', entryWire({ kind: 'USER_SIGNED_IN' })],
  [
    'an unknown wire field',
    entryWire({
      changes: [{ field: 'device_event_id', before: null, after: 'x' }],
    }),
  ],
  [
    'a camelCase field name on the wire',
    entryWire({ changes: [{ field: 'priorityRank', before: null, after: 1 }] }),
  ],
  [
    'an object value',
    entryWire({ changes: [{ field: 'name', before: null, after: { x: 1 } }] }),
  ],
  ['a malformed actor', entryWire({ actor_user: { id: 'x' } })],
  [
    'an unknown allocation source',
    entryWire({
      source: 'ALLOCATION',
      kind: 'ALLOCATED',
      allocation: {
        quantity: 1,
        source: 'ERP',
        is_manual_override: false,
        exceeds_demand: false,
        reverses_allocation_id: null,
        station_id: null,
      },
    }),
  ],
])(
  'a page with %s throws instead of rendering a wrong entry',
  async (_case, entry) => {
    answer = () => json(pageWire([entry]));
    // A malformed actor keeps the shared actor converter's own message.
    await expect(loadAuditTrail(PN)).rejects.toThrow(
      /^The server answered a malformed /,
    );
  },
);

test('an unknown next cursor source throws', async () => {
  answer = () =>
    json(
      pageWire([], {
        has_more: true,
        next_before_source: 'X',
        next_before_id: 3,
      }),
    );
  await expect(loadAuditTrail(PN)).rejects.toThrow(
    'The server answered a malformed audit trail entry.',
  );
});
