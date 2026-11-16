import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { addLinesToHotList, hotListIntakeNotice } from './hot-list-intake';

// Add to Hot list at Work Order creation (Phase 17): the lines join the
// bottom of the Hot list in line order through the Priority command —
// one ADD per line, each confirmed against the order the previous one
// committed; a list changed elsewhere is re-read once from the refusal,
// and any other failure stops with the lines that joined so far.

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

let calls: Call[];
let answers: Response[];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function entry(id: number, rank: number) {
  return {
    work_order_demand_id: id,
    rank,
    part_number: `PN-${id}`,
    work_order_id: 1,
    work_order_number: '007201',
    work_order_received_date: '2026-11-16',
    work_order_completed: false,
    request_type: 'NEW',
    job_numbers: [],
    requested_quantity: 5,
    allocated_quantity: 0,
    shortage_quantity: 5,
    active: true,
    released_quantity: 0,
    due_date: null,
    part_number_locations: [],
  };
}

function list(ids: number[]) {
  return ids.map((id, index) => entry(id, index + 1));
}

function hotList(ids: number[]): Response {
  return json({ department: { id: 1, name: 'Plating' }, entries: list(ids) });
}

function added(ids: number[]): Response {
  const id = ids[ids.length - 1];
  return json(
    {
      device_event_id: 'evt',
      action: 'ADD',
      created: true,
      changes: [
        {
          work_order_demand_id: id,
          part_number: `PN-${id}`,
          work_order_number: '007201',
          previous_rank: null,
          new_rank: ids.length,
        },
      ],
      entries: list(ids),
    },
    201,
  );
}

beforeEach(() => {
  calls = [];
  answers = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body:
          typeof init?.body === 'string'
            ? (JSON.parse(init.body) as Record<string, unknown>)
            : undefined,
      });
      const next = answers.shift();
      if (next === undefined) throw new Error('no answer queued');
      return next;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function changes(): Record<string, unknown>[] {
  return calls
    .filter((call) => call.url === '/api/hot-list/changes')
    .map((call) => call.body as Record<string, unknown>);
}

test('each line joins the bottom in line order, confirmed against the previous commit', async () => {
  answers = [hotList([7]), added([7, 31]), added([7, 31, 32])];
  expect(await addLinesToHotList([31, 32])).toEqual({ added: 2, error: null });
  expect(calls[0]).toMatchObject({ url: '/api/hot-list', method: 'GET' });
  const posted = changes();
  expect(
    posted.map(({ action, expected_order, new_order }) => ({
      action,
      expected_order,
      new_order,
    })),
  ).toEqual([
    { action: 'ADD', expected_order: [7], new_order: [7, 31] },
    { action: 'ADD', expected_order: [7, 31], new_order: [7, 31, 32] },
  ]);
  // One idempotency key per change.
  expect(new Set(posted.map((body) => body.device_event_id)).size).toBe(2);
});

test('a list changed elsewhere is re-read once from the refusal, under a new key', async () => {
  answers = [
    hotList([7]),
    json(
      {
        detail: 'The Hot list was changed meanwhile.',
        hot_list_changed: true,
        entries: list([7, 9]),
      },
      409,
    ),
    added([7, 9, 31]),
  ];
  expect(await addLinesToHotList([31])).toEqual({ added: 1, error: null });
  const posted = changes();
  expect(posted.map((body) => body.expected_order)).toEqual([[7], [7, 9]]);
  expect(posted[1].new_order).toEqual([7, 9, 31]);
  expect(posted[0].device_event_id).not.toBe(posted[1].device_event_id);
});

test('a refusal stops with the lines that joined so far and the server message', async () => {
  answers = [
    hotList([]),
    added([31]),
    json({ detail: 'Demand line 32 is not eligible for the Hot list.' }, 409),
  ];
  expect(await addLinesToHotList([31, 32, 33])).toEqual({
    added: 1,
    error: 'Demand line 32 is not eligible for the Hot list.',
  });
  expect(changes()).toHaveLength(2);
});

test('an unanswered change may have joined, and the outcome says so', async () => {
  answers = [hotList([]), json({ detail: 'Bad gateway.' }, 502)];
  const outcome = await addLinesToHotList([31]);
  expect(outcome.added).toBe(0);
  expect(outcome.error).toBe(
    'The server did not answer while a line was being added, so it may or may not have joined.',
  );
});

test('a failed read of the Hot list adds nothing', async () => {
  answers = [json({ detail: 'There is no active Department.' }, 404)];
  expect(await addLinesToHotList([31])).toEqual({
    added: 0,
    error: 'There is no active Department.',
  });
  expect(changes()).toHaveLength(0);
});

test('the notice names how far the lines got', () => {
  expect(hotListIntakeNotice({ added: 1, error: null }, 1)).toBe(
    '🔥 Its line joined the bottom of the Hot list.',
  );
  expect(hotListIntakeNotice({ added: 3, error: null }, 3)).toBe(
    '🔥 Its 3 lines joined the bottom of the Hot list.',
  );
  expect(hotListIntakeNotice({ added: 1, error: 'Refused.' }, 3)).toBe(
    '⚠ 1 of 3 lines joined the Hot list: Refused. Add the rest in Priority.',
  );
});
