import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../../app/connectivity-context';
import { TrackingView } from './TrackingView';
import {
  SEARCH_DEBOUNCE_MS,
  TRACKING_REFRESH_MS,
  describeMovement,
} from './tracking-logic';

// PN Tracking on the real feed (GUI_DESIGN §7, Phase 11): the polled
// list with its server-side filters and long-data paging, the loading /
// error / empty / stale states, the modeless detail overlay on the
// polled detail read (route steps and arrows as separate sibling flex
// items, the Floating trace with a repeated Area and the Repair marker,
// the PLANNED snapshot with its deviation, the immutable Movement
// history with a reversed original kept visible, the ready-to-transfer
// presentation kept distinct from Stockroom completion), and the v14
// selection / overlay interaction unchanged.

// ---------------------------------------------------------------------------
// Wire fixtures (the backend's snake_case shapes)
// ---------------------------------------------------------------------------

const LATHE = {
  id: 3,
  name: 'Lathe',
  color: 'var(--a-lathe)',
  is_terminal: false,
};
const CUT = { id: 2, name: 'Cut', color: 'var(--a-cut)', is_terminal: false };
const MATERIAL = {
  id: 1,
  name: 'Material',
  color: 'var(--a-material)',
  is_terminal: false,
};
const DEBURR = {
  id: 5,
  name: 'Deburr',
  color: 'var(--a-deburr)',
  is_terminal: false,
};
const STOCKROOM = {
  id: 8,
  name: 'Stockroom',
  color: 'var(--a-stockroom)',
  is_terminal: true,
};
const OPERATION = { id: 30, code: 'TURN', name: 'Turning', is_external: false };
const LATHE_M1 = { id: 11, name: 'Lathe M1' };

function demand(overrides: Record<string, unknown> = {}) {
  return {
    work_order_id: 1,
    work_order_number: '007001',
    work_order_demand_id: 10,
    request_type: 'NEW',
    requested_quantity: 10,
    allocated_quantity: 0,
    job_numbers: ['18112'],
    due_date: '2030-07-24',
    priority_rank: 1,
    ...overrides,
  };
}

function listPayload() {
  return {
    rows: [
      {
        part_number: '2027-60-8114-00',
        has_master: true,
        barcode_value: 'PF:PN:2027-60-8114-00',
        hot_rank: 1,
        demands: [
          demand(),
          demand({
            work_order_id: 2,
            work_order_number: null,
            work_order_demand_id: 11,
            request_type: 'MODIFY',
            requested_quantity: 5,
            due_date: null,
            priority_rank: null,
          }),
        ],
        distribution: [
          { area: CUT, quantity: 4, stocked: false },
          { area: LATHE, quantity: 6, stocked: false },
        ],
        active_quantity: 10,
        stocked_quantity: 0,
        allocated_quantity: 0,
        available_stocked_quantity: 0,
        scrapped_quantity: 1,
        next_due_date: '2030-07-24',
        status: 'ACTIVE',
      },
      {
        part_number: '142-260',
        has_master: false,
        barcode_value: 'PF:PN:142-260',
        hot_rank: null,
        demands: [],
        distribution: [{ area: STOCKROOM, quantity: 20, stocked: true }],
        active_quantity: 0,
        stocked_quantity: 20,
        allocated_quantity: 20,
        available_stocked_quantity: 0,
        scrapped_quantity: 0,
        next_due_date: null,
        status: 'COMPLETED',
      },
    ],
    total: 2,
    offset: 0,
    limit: 100,
    has_more: false,
  };
}

function movement(overrides: Record<string, unknown>) {
  return {
    id: 1,
    quantity_flow_id: 140,
    movement_type: 'TRANSFERRED',
    quantity: 1,
    from_area: null,
    to_area: LATHE,
    operation: OPERATION,
    source_machine: null,
    destination_machine: null,
    station_id: 'LATHE-ST-1',
    occurred_at: '2030-07-22T15:05:00Z',
    device_event_id: 'evt',
    command_sequence: 1,
    movement_reason: null,
    reason: null,
    reverses_movement_id: null,
    reversed_by_movement_id: null,
    assigned_route_step: null,
    route_deviation: null,
    lineage: [],
    demand: null,
    ...overrides,
  };
}

function detailPayload() {
  const step = (id: number, area: typeof LATHE, state: string) => ({
    id,
    sequence: id * 10,
    area,
    operation: null,
    expected_duration: null,
    state,
  });
  const trace = (
    movementId: number,
    area: typeof LATHE,
    extra: Record<string, unknown> = {},
  ) => ({
    movement_id: movementId,
    quantity_flow_id: 140,
    movement_type: 'TRANSFERRED',
    area,
    occurred_at: '2030-07-20T08:12:00Z',
    repair: false,
    inherited: false,
    ...extra,
  });
  return {
    part_number: '2027-60-8114-00',
    master: {
      part_number: '2027-60-8114-00',
      created_at: '2030-07-01T00:00:00Z',
    },
    barcode_value: 'PF:PN:2027-60-8114-00',
    status: 'ACTIVE',
    demands: [
      { ...demand(), released_quantity: 10, shortage: 10 },
      {
        ...demand({
          work_order_id: 2,
          work_order_number: '007008',
          work_order_demand_id: 11,
          requested_quantity: 5,
          due_date: '2030-08-02',
          priority_rank: null,
          job_numbers: [],
        }),
        released_quantity: 0,
        shortage: 5,
      },
    ],
    locations: [
      {
        area: CUT,
        machine: null,
        activity: null,
        quantity: 4,
        state: 'PROCESSING',
        since: '2030-07-22T10:00:00Z',
      },
      {
        area: LATHE,
        machine: LATHE_M1,
        activity: null,
        quantity: 3,
        state: 'MACHINE',
        since: '2030-07-22T13:05:00Z',
      },
      {
        area: LATHE,
        machine: null,
        activity: null,
        quantity: 2,
        state: 'QUEUE',
        since: '2030-07-22T11:20:00Z',
      },
      {
        area: LATHE,
        machine: LATHE_M1,
        activity: null,
        quantity: 1,
        state: 'DONE',
        since: '2030-07-22T15:05:00Z',
      },
    ],
    stocked: [],
    active_quantity: 10,
    stocked_quantity: 0,
    allocated_quantity: 0,
    available_stocked_quantity: 0,
    scrapped_quantity: 1,
    introduced_quantity: 11,
    flows: {
      flows: [
        {
          id: 140,
          quantity: 6,
          status: 'ACTIVE',
          route_mode: 'PLANNED',
          created_at: '2030-07-12T08:02:00Z',
          closed_at: null,
          position: {
            area: LATHE,
            machine: null,
            operation: OPERATION,
            activity: null,
            state: 'QUEUE',
            since: '2030-07-22T11:20:00Z',
          },
          parents: [],
          children: [{ quantity_flow_id: 141, relation: 'SPLIT' }],
          trace: [trace(1, MATERIAL), trace(2, CUT), trace(3, LATHE)],
          route_steps: [
            step(1, MATERIAL, 'DONE'),
            step(2, CUT, 'DONE'),
            step(3, LATHE, 'CURRENT'),
            step(4, DEBURR, 'FUTURE'),
            step(5, STOCKROOM, 'FUTURE'),
          ],
          source_template: { id: 7, name: 'Bracket std v3' },
          off_route: false,
          deviations: [],
        },
        {
          id: 141,
          quantity: 4,
          status: 'ACTIVE',
          route_mode: 'FLOATING',
          created_at: '2030-07-20T15:22:00Z',
          closed_at: null,
          position: {
            area: CUT,
            machine: null,
            operation: OPERATION,
            activity: null,
            state: 'PROCESSING',
            since: '2030-07-22T13:40:00Z',
          },
          parents: [{ quantity_flow_id: 140, relation: 'SPLIT' }],
          children: [],
          // The repeated Cut visit is a confirmed Repair return; the
          // three earlier arrivals were recorded on the source flow.
          trace: [
            trace(1, MATERIAL, { inherited: true }),
            trace(2, CUT, { inherited: true }),
            trace(3, LATHE, { inherited: true }),
            trace(9, CUT, { quantity_flow_id: 141, repair: true }),
          ],
          route_steps: [],
          source_template: null,
          off_route: false,
          deviations: [],
        },
      ],
      total: 2,
      has_more: false,
      next_before_flow_id: null,
    },
    allocations: {
      allocations: [],
      total: 0,
      has_more: false,
      next_before_allocation_id: null,
    },
    movements: {
      movements: [
        movement({
          id: 12,
          movement_type: 'AREA_COMPLETED',
          quantity: 1,
          from_area: LATHE,
          source_machine: LATHE_M1,
        }),
        movement({
          id: 11,
          movement_type: 'SCRAPPED',
          quantity: 1,
          from_area: LATHE,
          reason: 'tool crash — gouged face',
          occurred_at: '2030-07-22T14:10:00Z',
        }),
        movement({
          id: 9,
          quantity_flow_id: 141,
          quantity: 4,
          from_area: LATHE,
          to_area: CUT,
          movement_reason: 'REPAIR',
          reason: 'shoulder cut short — recut required',
          occurred_at: '2030-07-22T13:40:00Z',
        }),
        movement({
          id: 6,
          movement_type: 'REVERSED',
          quantity: 2,
          from_area: LATHE,
          reverses_movement_id: 5,
          occurred_at: '2030-07-21T09:41:00Z',
        }),
        movement({
          id: 5,
          movement_type: 'ASSIGNED_TO_MACHINE',
          quantity: 2,
          from_area: LATHE,
          destination_machine: { id: 12, name: 'Lathe M2' },
          reversed_by_movement_id: 6,
          occurred_at: '2030-07-21T09:38:00Z',
        }),
      ],
      total: 9,
      has_more: true,
      next_before_movement_id: 5,
    },
    // The same history restricted to scrap: the newest event first, an
    // older (undone) one on the next page.
    scrap_history: {
      movements: [
        movement({
          id: 11,
          movement_type: 'SCRAPPED',
          quantity: 1,
          from_area: LATHE,
          reason: 'tool crash — gouged face',
          occurred_at: '2030-07-22T14:10:00Z',
        }),
      ],
      total: 2,
      has_more: true,
      next_before_movement_id: 11,
    },
  };
}

function olderScrapPayload() {
  return {
    movements: [
      movement({
        id: 4,
        movement_type: 'SCRAPPED',
        quantity: 2,
        from_area: MATERIAL,
        to_area: MATERIAL,
        reason: 'wrong material',
        reversed_by_movement_id: 7,
        occurred_at: '2030-07-13T09:00:00Z',
      }),
    ],
    total: 2,
    has_more: false,
    next_before_movement_id: null,
  };
}

function olderPayload() {
  return {
    movements: [
      movement({
        id: 1,
        movement_type: 'RECEIVED',
        quantity: 11,
        to_area: MATERIAL,
        station_id: null,
        occurred_at: '2030-07-12T08:02:00Z',
        assigned_route_step: { id: 1, sequence: 10 },
        demand: {
          work_order_id: 1,
          work_order_number: '007001',
          work_order_demand_id: 10,
          request_type: 'NEW',
        },
      }),
    ],
    total: 9,
    has_more: false,
    next_before_movement_id: null,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

const EMPTY_LIST = {
  rows: [],
  total: 0,
  offset: 0,
  limit: 100,
  has_more: false,
};

function stubFetch(
  answer: (url: string) => Response | Promise<Response> = defaultAnswer,
) {
  const fetchMock = vi.fn((input: RequestInfo | URL) =>
    Promise.resolve(answer(String(input))),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function defaultAnswer(url: string): Response {
  if (url.startsWith('/api/tracking/detail'))
    return jsonResponse(detailPayload());
  if (url.startsWith('/api/tracking/movements')) {
    return jsonResponse(
      url.includes('movement_type=SCRAPPED')
        ? olderScrapPayload()
        : olderPayload(),
    );
  }
  if (url.startsWith('/api/tracking')) return jsonResponse(listPayload());
  if (url === '/api/areas') return jsonResponse([]);
  if (url === '/api/operations') return jsonResponse([]);
  if (url === '/api/machines') return jsonResponse([]);
  return jsonResponse({ detail: `unexpected ${url}` }, 404);
}

function trackingCalls(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.startsWith('/api/tracking'));
}

/** Render the view with a healthy shared connectivity state. */
async function renderTracking(
  status: 'connected' | 'unavailable' = 'connected',
) {
  const result = render(
    <ConnectivityContext.Provider value={{ status, retry: () => {} }}>
      <TrackingView />
    </ConnectivityContext.Provider>,
  );
  // The first feed answer resolves in a microtask.
  await act(async () => {});
  return result;
}

/** Select the first result row and let its detail read resolve. */
async function openFirstRow() {
  const row = document.querySelector('.tk-table .rowbtn') as HTMLElement;
  fireEvent.click(row);
  await act(async () => {});
  return row;
}

function flowBlock(id: string): Element {
  const block = Array.from(document.querySelectorAll('.qflow')).find(
    (el) => el.querySelector('.qf-id')?.textContent === id,
  );
  expect(block).toBeDefined();
  return block!;
}

beforeEach(() => {
  window.history.replaceState({}, '', '/management/tracking');
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// The list feed
// ---------------------------------------------------------------------------

test('the list reads the default page and renders the derived rows', async () => {
  const fetchMock = stubFetch();
  await renderTracking();

  expect(trackingCalls(fetchMock)).toEqual(['/api/tracking?limit=100']);
  const rows = document.querySelectorAll('.tk-table tbody tr');
  expect(rows.length).toBe(2);
  const first = rows[0] as HTMLElement;
  // Hot presentation before the PN, both open demands (a blank Work
  // Order Number as `—`), the distribution dots, the figures, the next
  // due date and the status pill.
  expect(first.querySelector('.hot')?.textContent).toBe('🔥#1');
  expect(first.textContent).toContain('2027-60-8114-00');
  expect(first.querySelector('.demandcell')?.textContent).toContain(
    '007001 · 10',
  );
  expect(first.querySelector('.demandcell')?.textContent).toContain('— · 5');
  expect(first.querySelectorAll('.demandcell .typechip.modify').length).toBe(1);
  expect(first.querySelector('.distmini')?.textContent).toContain('Cut 4');
  expect(first.querySelector('.distmini')?.textContent).toContain('Lathe 6');
  expect(first.querySelector('[data-label="Active qty"]')?.textContent).toBe(
    '10',
  );
  expect(first.querySelector('[data-label="Scrapped"]')?.textContent).toBe('1');
  expect(first.querySelector('[data-label="Due (next)"]')?.textContent).toBe(
    'Jul 24',
  );
  expect(first.querySelector('.status')?.textContent).toBe('Active');

  const second = rows[1] as HTMLElement;
  expect(second.querySelector('[data-label="Due (next)"]')?.textContent).toBe(
    '—',
  );
  expect(second.querySelector('[data-label="Scrapped"]')?.textContent).toBe(
    '—',
  );
  expect(second.querySelector('.status')?.textContent).toBe('Completed');
  expect(document.querySelector('.tk-feed')?.textContent).toContain('Live');
  expect(document.querySelector('.tk-paging')?.textContent).toBe(
    'Showing 2 of 2 PNs',
  );
  // Nothing is selected until a row is chosen.
  expect(document.querySelector('.tk-right')).toBeNull();
});

test('search reaches the server debounced and the selects at once', async () => {
  vi.useFakeTimers();
  const fetchMock = stubFetch();
  await renderTracking();

  fireEvent.change(screen.getByLabelText('Search PN, WO, Job Number'), {
    target: { value: '0070' },
  });
  await act(async () => {});
  expect(trackingCalls(fetchMock)).toEqual(['/api/tracking?limit=100']);
  await act(async () => {
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
  });
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking?search=0070&limit=100',
  );

  fireEvent.change(screen.getByLabelText('Status'), {
    target: { value: 'ALL' },
  });
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking?search=0070&status=ALL&limit=100',
  );
  fireEvent.change(screen.getByLabelText('Priority'), {
    target: { value: 'HOT' },
  });
  fireEvent.change(screen.getByLabelText('Due'), {
    target: { value: 'OVERDUE' },
  });
  fireEvent.change(screen.getByLabelText('Request Type'), {
    target: { value: 'MODIFY' },
  });
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking?search=0070&request_type=MODIFY&hot_only=true&status=ALL&due=OVERDUE&limit=100',
  );
});

test('the filter selects offer the configured Areas, Operations and Machines', async () => {
  const fetchMock = stubFetch((url) => {
    if (url === '/api/areas') {
      return jsonResponse([
        {
          id: 3,
          department_id: 1,
          name: 'Lathe',
          barcode_value: null,
          description: null,
          color: null,
          icon_url: null,
          is_terminal: false,
          is_active: true,
        },
        {
          id: 4,
          department_id: 1,
          name: 'Old',
          barcode_value: null,
          description: null,
          color: null,
          icon_url: null,
          is_terminal: false,
          is_active: false,
        },
      ]);
    }
    if (url === '/api/operations') {
      return jsonResponse([
        {
          id: 30,
          area_id: 3,
          code: 'TURN',
          name: 'Turning',
          description: null,
          default_expected_duration: null,
          is_external: false,
          is_active: true,
        },
      ]);
    }
    if (url === '/api/machines') {
      return jsonResponse([
        {
          id: 11,
          area_id: 3,
          name: 'Lathe M1',
          asset_tag: 'AB-0001',
          barcode_value: 'PF:MACHINE:AB-0001',
          maintenance_since: null,
          maintenance_note: null,
          maintenance_expected_return: null,
          state_changed_at: '2030-01-01T00:00:00Z',
          assigned_quantity: 0,
          operational_state: 'IDLE',
          retired_on: null,
        },
        {
          id: 12,
          area_id: 3,
          name: 'Gone',
          asset_tag: 'AB-0002',
          barcode_value: 'PF:MACHINE:AB-0002',
          maintenance_since: null,
          maintenance_note: null,
          maintenance_expected_return: null,
          state_changed_at: '2030-01-01T00:00:00Z',
          assigned_quantity: 0,
          operational_state: 'IDLE',
          retired_on: '2030-02-02',
        },
      ]);
    }
    return defaultAnswer(url);
  });
  await renderTracking();

  const area = screen.getByLabelText('Area') as HTMLSelectElement;
  expect(Array.from(area.options, (o) => o.textContent)).toEqual([
    'Area: All',
    'Lathe',
  ]);
  const machine = screen.getByLabelText('Machine') as HTMLSelectElement;
  expect(Array.from(machine.options, (o) => o.textContent)).toEqual([
    'Machine: All',
    'Lathe M1',
  ]);
  expect(
    Array.from(
      (screen.getByLabelText('Operation') as HTMLSelectElement).options,
      (o) => o.textContent,
    ),
  ).toEqual(['Operation: All', 'Turning']);

  fireEvent.change(area, { target: { value: '3' } });
  fireEvent.change(machine, { target: { value: '11' } });
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking?area_id=3&machine_id=11&limit=100',
  );
});

test('the loading state shows under the header until the first page arrives', async () => {
  let resolve: (value: Response) => void = () => {};
  stubFetch((url) =>
    url.startsWith('/api/tracking')
      ? new Promise<Response>((r) => {
          resolve = r;
        })
      : defaultAnswer(url),
  );
  await renderTracking();

  expect(
    screen.getByRole('status', { name: 'Loading PN Tracking' }),
  ).toBeInTheDocument();
  expect(screen.getByText('PN Tracking')).toBeInTheDocument();
  // Not yet a live feed: no complete page is on screen.
  expect(document.querySelector('.tk-feed')?.textContent).toContain(
    'Feed stale — reconnecting',
  );
  await act(async () => {
    resolve(jsonResponse(listPayload()));
  });
  expect(document.querySelector('.tk-table')).not.toBeNull();
  expect(document.querySelector('.tk-feed')?.textContent).toContain('Live');
});

test('a failed first load is the error state with Retry', async () => {
  let failures = 1;
  stubFetch((url) => {
    if (url.startsWith('/api/tracking') && failures > 0) {
      failures -= 1;
      return jsonResponse({ detail: 'Tracking is unavailable.' }, 503);
    }
    return defaultAnswer(url);
  });
  await renderTracking();

  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('PN Tracking data could not be loaded.');
  expect(alert).toHaveTextContent('Tracking is unavailable.');
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(document.querySelector('.tk-table')).not.toBeNull();
});

test('an empty result is the explicit empty state', async () => {
  stubFetch((url) =>
    url.startsWith('/api/tracking')
      ? jsonResponse(EMPTY_LIST)
      : defaultAnswer(url),
  );
  await renderTracking();
  expect(
    screen.getByText(/No PNs match the current filters — clear filters/),
  ).toBeInTheDocument();
});

test('a failed refresh keeps the rows and marks the feed stale until the next good answer', async () => {
  vi.useFakeTimers();
  let fail = false;
  stubFetch((url) => {
    if (url.startsWith('/api/tracking') && fail) {
      return jsonResponse({ detail: 'gone' }, 503);
    }
    return defaultAnswer(url);
  });
  await renderTracking();
  expect(document.querySelector('.tk-feed')?.textContent).toContain('Live');

  fail = true;
  await act(async () => {
    vi.advanceTimersByTime(TRACKING_REFRESH_MS);
  });
  await act(async () => {});
  expect(document.querySelectorAll('.tk-table tbody tr').length).toBe(2);
  expect(document.querySelector('.tk-feed')?.textContent).toContain(
    'Feed stale — reconnecting',
  );

  fail = false;
  await act(async () => {
    vi.advanceTimersByTime(TRACKING_REFRESH_MS);
  });
  await act(async () => {});
  expect(document.querySelector('.tk-feed')?.textContent).toContain('Live');
});

test('an unhealthy connection reads as a stale feed over the kept rows', async () => {
  await renderTracking('unavailable');
  expect(document.querySelectorAll('.tk-table tbody tr').length).toBe(2);
  expect(document.querySelector('.tk-feed')?.textContent).toContain(
    'Feed stale — reconnecting',
  );
});

test('Show more widens the page to the server bound and then asks to narrow the search', async () => {
  const fetchMock = stubFetch((url) => {
    if (url.startsWith('/api/tracking?')) {
      const limit = Number(new URL(url, 'http://x').searchParams.get('limit'));
      return jsonResponse({
        ...listPayload(),
        total: 450,
        limit,
        has_more: true,
      });
    }
    return defaultAnswer(url);
  });
  await renderTracking();

  expect(
    document.querySelector('.tk-left > .tk-paging')?.textContent,
  ).toContain('Showing 2 of 450 PNs');
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe('/api/tracking?limit=200');
  expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
  expect(screen.getByText(/Only the first 200 are listed/)).toBeInTheDocument();
});

test('the state previews render without any request', async () => {
  const fetchMock = stubFetch();
  window.history.replaceState({}, '', '/management/tracking?state=long');
  await renderTracking();
  expect(trackingCalls(fetchMock)).toEqual([]);
  expect(document.querySelectorAll('.tk-table tbody tr').length).toBe(35);
});

// ---------------------------------------------------------------------------
// The detail overlay
// ---------------------------------------------------------------------------

test('selecting a row reads its detail and renders the demand, locations and figures', async () => {
  const fetchMock = stubFetch();
  await renderTracking();
  await openFirstRow();

  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking/detail?part_number=2027-60-8114-00&movements_limit=50',
  );
  const panel = document.querySelector('.tk-right') as HTMLElement;
  expect(panel.querySelector('h2')?.textContent).toBe('2027-60-8114-00');
  expect(panel.textContent).toContain('PF:PN:2027-60-8114-00');

  const demandRows = panel.querySelectorAll('table.demand tbody tr');
  expect(demandRows.length).toBe(2);
  expect(demandRows[0].textContent).toContain('007001');
  expect(
    demandRows[0].querySelector('[data-label="Released"]')?.textContent,
  ).toBe('10');
  expect(
    demandRows[0].querySelector('[data-label="Shortage"]')?.textContent,
  ).toBe('10');
  expect(
    demandRows[0].querySelector('[data-label="Priority"]')?.textContent,
  ).toBe('🔥#1');
  expect(
    demandRows[1].querySelector('[data-label="Priority"]')?.textContent,
  ).toBe('—');
  expect(panel.textContent).toContain('Allocated 0 / 15 requested');

  // Current quantity by Area — every holding state distinct.
  const dist = Array.from(panel.querySelectorAll('.dist .drow'), (row) => ({
    name: row.querySelector('.nm')?.textContent?.trim(),
    qty: row.querySelector('.q')?.textContent,
  }));
  expect(dist).toEqual([
    { name: 'Cut processing', qty: '4' },
    { name: 'Lathe M1 on machine', qty: '3' },
    { name: 'Lathe queue', qty: '2' },
    { name: 'Lathe ready to transfer', qty: '1' },
  ]);
  // The §11 reconciliation from the server's figures.
  expect(panel.textContent).toContain(
    'introduced 11 = active 10 + stocked 0 + scrapped 1',
  );
});

test('route steps and arrows are separate siblings in alternating order', async () => {
  await renderTracking();
  await openFirstRow();

  const routes = Array.from(document.querySelectorAll('.route'));
  expect(routes.length).toBe(2); // one per Quantity Flow

  for (const route of routes) {
    const children = Array.from(route.children);
    // Document order alternates step, arrow, step, … — every child is
    // one of the two, so an arrow is never nested inside a step wrapper.
    children.forEach((el, i) => {
      const expected = i % 2 === 0 ? 'rstep' : 'rarrow';
      expect(el.classList.contains(expected)).toBe(true);
    });
    // Starts and ends with a step: n steps and n-1 arrows.
    expect(children.length % 2).toBe(1);
  }

  const planned = flowBlock('QF-140');
  expect(planned.querySelectorAll('.route > .rstep').length).toBe(5);
  expect(planned.querySelectorAll('.route > .rarrow').length).toBe(4);
  expect(planned.querySelector('.routechip')?.textContent).toBe(
    'PLANNED — snapshot',
  );
  expect(planned.textContent).toContain(
    'Planned Route “Bracket std v3” (snapshot)',
  );
  expect(planned.textContent).toContain('Actual path: Material → Cut → Lathe');
  expect(planned.textContent).toContain('SPLIT into QF-141');
});

test('the Floating trace keeps repeated Areas and the Repair marker', async () => {
  await renderTracking();
  await openFirstRow();

  const floating = flowBlock('QF-141');
  const steps = Array.from(floating.querySelectorAll('.rstep'), (el) =>
    el.textContent?.trim(),
  );
  // Cut appears twice — the trace is Movement history, and the second
  // visit is the explicitly marked Repair return.
  expect(steps).toEqual(['Material', 'Cut', 'Lathe', 'Cut ⟲ REPAIR']);
  expect(floating.querySelector('.rstep.repair .repairmark')).not.toBeNull();
  expect(floating.querySelector('.routechip')?.textContent).toBe(
    'FLOATING — actual trace',
  );
  expect(floating.querySelector('.qf-pos')?.textContent).toContain(
    'SPLIT from QF-140',
  );
  // The last arrival is the current position of an active flow.
  expect(floating.querySelector('.rstep.cur')?.textContent).toContain('Cut');
});

test('the finished-rack state never adds a route step', async () => {
  await renderTracking();
  await openFirstRow();

  // QF-140 holds the ready-to-transfer piece: its route keeps exactly
  // its five Area steps — completion happens inside the Lathe step.
  const planned = flowBlock('QF-140');
  const steps = Array.from(
    planned.querySelectorAll('.rstep'),
    (el) => el.textContent ?? '',
  );
  expect(steps).toEqual(['Material', 'Cut', 'Lathe', 'Deburr', 'Stockroom']);

  // No trace anywhere gains a fake finished-rack step.
  for (const route of document.querySelectorAll('.route')) {
    expect(route.textContent).not.toMatch(/rack/i);
  }
});

test('ready-to-transfer is presented distinctly and never as Stocked', async () => {
  await renderTracking();
  await openFirstRow();

  const done = document.querySelector('.dist .drow.done');
  expect(done).not.toBeNull();
  expect(done!.textContent).toContain('ready to transfer');
  expect(done!.querySelector('.q')?.textContent).toBe('1');
  expect(done!.textContent).not.toMatch(/stocked/i);
  // The Area is the location — the Machine no longer holds the piece.
  expect(done!.querySelector('.nm')?.textContent).not.toContain('Lathe M1');

  const note = document.querySelector('.donenote');
  expect(note?.textContent).toContain(
    'Completed processing at Lathe M1 in Lathe — ready to transfer',
  );
  expect(note?.textContent).not.toMatch(/stocked/i);
});

test('Movement history lists every type with its badge and keeps a reversed original visible', async () => {
  await renderTracking();
  await openFirstRow();

  const items = Array.from(document.querySelectorAll('.mv.history li'));
  expect(items.length).toBe(5);
  const badge = items[0].querySelector('.mtype')!;
  expect(badge.textContent).toBe('AREA_COMPLETED');
  expect(badge.classList.contains('don')).toBe(true);
  expect(items[0].textContent).toContain(
    'Completed processing at Lathe M1 in Lathe — ready to transfer · qty 1 · QF-140 · LATHE-ST-1',
  );
  expect(items[1].textContent).toContain(
    'Scrapped 1 at Lathe · QF-140 · reason: tool crash — gouged face',
  );
  // The Repair transfer carries its explicit badge and reason.
  expect(items[2].querySelectorAll('.mtype')[1].textContent).toBe('REPAIR');
  expect(items[2].textContent).toContain('Lathe → Cut · qty 4 · QF-141');
  expect(items[2].textContent).toContain(
    'reason: shoulder cut short — recut required',
  );
  // The undone assignment stays in the history, marked, beside the
  // REVERSED row that undid it — nothing is hidden or rewritten.
  expect(items[3].querySelector('.mtype.rev')?.textContent).toBe('REVERSED');
  expect(items[3].textContent).toContain('Reverses Movement #5');
  expect(items[4].classList.contains('reversed')).toBe(true);
  expect(items[4].querySelector('.mtype.rev')?.textContent).toBe('REVERSED');
  expect(items[4].textContent).toContain('Lathe queue → Lathe M2 · qty 2');
  const paging = document
    .querySelector('.mv.history')!
    .parentElement!.querySelector('.tk-paging');
  expect(paging?.textContent).toContain('Showing 5 of 9 Movements');
});

test('older Movement pages append below the first page on request', async () => {
  const fetchMock = stubFetch();
  await renderTracking();
  await openFirstRow();

  fireEvent.click(screen.getByRole('button', { name: 'Show older Movements' }));
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking/movements?part_number=2027-60-8114-00&before=5&limit=50',
  );
  const items = Array.from(document.querySelectorAll('.mv.history li'));
  expect(items.length).toBe(6);
  expect(items[5].querySelector('.mtype')?.textContent).toBe('RECEIVED');
  expect(items[5].textContent).toContain(
    'Received into Material · qty 11 · QF-140 · WO 007001 release · Planned Route step 10',
  );
  // The last page: the control is gone.
  expect(
    screen.queryByRole('button', { name: 'Show older Movements' }),
  ).toBeNull();
  const paging = document
    .querySelector('.mv.history')!
    .parentElement!.querySelector('.tk-paging');
  expect(paging?.textContent).toContain('Showing 6 of 9 Movements');
});

test('the Scrap history lists every scrap event with its reversed state and pages on the same history', async () => {
  const fetchMock = stubFetch();
  await renderTracking();
  await openFirstRow();

  const list = document.querySelector('.mv.scrap') as HTMLElement;
  const section = list.closest('.tk-sec') as HTMLElement;
  expect(section.textContent).toContain('Cumulative scrapped: 1 pcs');
  let items = list.querySelectorAll('li');
  expect(items.length).toBe(1);
  expect(items[0].querySelector('.mtype')?.textContent).toBe('SCRAPPED');
  expect(items[0].textContent).toContain('Jul 22 ');
  expect(items[0].textContent).toContain(
    'Scrapped 1 at Lathe · QF-140 · reason: tool crash — gouged face · LATHE-ST-1',
  );
  expect(section.textContent).toContain('Showing 1 of 2 scrap events');

  fireEvent.click(
    screen.getByRole('button', { name: 'Show older scrap events' }),
  );
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking/movements?part_number=2027-60-8114-00&before=11&limit=20&movement_type=SCRAPPED',
  );
  items = list.querySelectorAll('li');
  expect(items.length).toBe(2);
  // The undone scrap stays listed — timestamp, quantity, Area and reason
  // intact — marked REVERSED and quieter; the cumulative figure above
  // is the net one.
  expect(items[1].classList.contains('reversed')).toBe(true);
  expect(items[1].querySelector('.mtype.rev')?.textContent).toBe('REVERSED');
  expect(items[1].textContent).toContain(
    'Scrapped 2 at Material · QF-140 · reason: wrong material',
  );
  expect(section.textContent).toContain('Showing 2 of 2 scrap events');
  expect(
    screen.queryByRole('button', { name: 'Show older scrap events' }),
  ).toBeNull();
});

test('closed Quantity Flows and allocation entries page below the first page', async () => {
  const workOrder = {
    work_order_id: 1,
    work_order_number: '007001',
    work_order_demand_id: 10,
    request_type: 'NEW',
  };
  const fetchMock = stubFetch((url) => {
    if (url.startsWith('/api/tracking/detail')) {
      const base = detailPayload();
      return jsonResponse({
        ...base,
        flows: {
          ...base.flows,
          total: 5,
          has_more: true,
          next_before_flow_id: 120,
        },
        allocations: {
          allocations: [
            {
              id: 31,
              quantity: 2,
              work_order: workOrder,
              source: 'STOCKROOM',
              is_manual_override: false,
              allocation_reason: null,
              reverses_allocation_id: null,
              reversed_by_allocation_id: null,
              station_id: 'STOCK-ST-1',
              allocated_at: '2030-07-23T08:00:00Z',
            },
          ],
          total: 2,
          has_more: true,
          next_before_allocation_id: 31,
        },
      });
    }
    if (url.startsWith('/api/tracking/flows')) {
      const closed = {
        ...detailPayload().flows.flows[0],
        id: 120,
        status: 'SPLIT',
        position: null,
        children: [],
      };
      return jsonResponse({
        flows: [closed],
        total: 5,
        has_more: false,
        next_before_flow_id: null,
      });
    }
    if (url.startsWith('/api/tracking/allocations')) {
      return jsonResponse({
        allocations: [
          {
            id: 30,
            quantity: 1,
            work_order: workOrder,
            source: 'MANAGEMENT',
            is_manual_override: true,
            allocation_reason: 'rush',
            reverses_allocation_id: null,
            reversed_by_allocation_id: 31,
            station_id: null,
            allocated_at: '2030-07-20T08:00:00Z',
          },
        ],
        total: 2,
        has_more: false,
        next_before_allocation_id: null,
      });
    }
    return defaultAnswer(url);
  });
  await renderTracking();
  await openFirstRow();

  expect(document.querySelectorAll('.qflow').length).toBe(2);
  fireEvent.click(
    screen.getByRole('button', { name: 'Show older Quantity Flows' }),
  );
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking/flows?part_number=2027-60-8114-00&before=120&limit=50',
  );
  expect(document.querySelectorAll('.qflow').length).toBe(3);
  expect(document.querySelectorAll('.qflow.closed').length).toBe(1);
  expect(
    screen.queryByRole('button', { name: 'Show older Quantity Flows' }),
  ).toBeNull();

  fireEvent.click(
    screen.getByRole('button', { name: 'Show older allocation entries' }),
  );
  await act(async () => {});
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking/allocations?part_number=2027-60-8114-00&before=31&limit=100',
  );
  const entries = Array.from(document.querySelectorAll('.tk-sec')).find((el) =>
    el.textContent?.includes('Stocked & Allocation history'),
  )!;
  const rows = entries.querySelectorAll('.mv li');
  expect(rows.length).toBe(2);
  expect(rows[1].classList.contains('reversed')).toBe(true);
  expect(rows[1].textContent).toContain(
    '1 pcs · WO 007001 · management · manual override · reason: rush',
  );
  expect(entries.textContent).toContain('Showing 2 of 2 allocation entries');
});

test('the Movement history stays read-only — its only control pages the history', async () => {
  await renderTracking();
  await openFirstRow();

  const section = document.querySelector('.mv.history')!.closest('.tk-sec')!;
  // Immutable audit data: no edit, delete, or any other affordance
  // besides the paging control exists in the history section.
  const controls = section.querySelectorAll('button, a, input, select');
  expect(controls.length).toBe(1);
  expect(controls[0].textContent).toBe('Show older Movements');
  // No correction actions are offered before authorization exists.
  expect(document.querySelector('.tk-actions')).toBeNull();
});

test('a detail read that fails shows its error with Retry inside the panel', async () => {
  let failures = 1;
  stubFetch((url) => {
    if (url.startsWith('/api/tracking/detail') && failures > 0) {
      failures -= 1;
      return jsonResponse({ detail: 'Detail unavailable.' }, 503);
    }
    return defaultAnswer(url);
  });
  await renderTracking();
  await openFirstRow();

  const panel = document.querySelector('.tk-right') as HTMLElement;
  expect(within(panel).getByRole('alert')).toHaveTextContent(
    'Detail unavailable.',
  );
  fireEvent.click(within(panel).getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(panel.querySelector('table.demand')).not.toBeNull();
});

test('a PN whose master record is absent still renders its history', async () => {
  stubFetch((url) =>
    url.startsWith('/api/tracking/detail')
      ? jsonResponse({ ...detailPayload(), master: null })
      : defaultAnswer(url),
  );
  await renderTracking();
  await openFirstRow();

  const panel = document.querySelector('.tk-right') as HTMLElement;
  expect(panel.textContent).toContain(
    'no Part Number master record — history unaffected',
  );
  expect(panel.querySelectorAll('.mv.history li').length).toBe(5);
});

test('describeMovement states lineage, stocking and additions as recorded', () => {
  const split = describeMovement({
    ...toModel(
      movement({
        id: 3,
        movement_type: 'SPLIT',
        quantity: 11,
        to_area: CUT,
        lineage: [
          { parent_flow_id: 140, child_flow_id: 141, relation: 'SPLIT' },
          { parent_flow_id: 140, child_flow_id: 142, relation: 'SPLIT' },
        ],
      }),
    ),
  });
  expect(split).toBe('QF-140 (11) → QF-141 + QF-142 · at Cut · LATHE-ST-1');
  const child = describeMovement(
    toModel(
      movement({
        id: 4,
        quantity_flow_id: 141,
        movement_type: 'SPLIT',
        quantity: 4,
        to_area: CUT,
        lineage: [
          { parent_flow_id: 140, child_flow_id: 141, relation: 'SPLIT' },
        ],
      }),
    ),
  );
  expect(child).toBe('QF-141 (4) from QF-140 · at Cut · LATHE-ST-1');
  expect(
    describeMovement(
      toModel(
        movement({
          movement_type: 'STOCKED',
          quantity: 6,
          from_area: DEBURR,
          to_area: STOCKROOM,
        }),
      ),
    ),
  ).toBe('Deburr → Stockroom · qty 6 · QF-140 · stocked · LATHE-ST-1');
  expect(
    describeMovement(
      toModel(
        movement({
          movement_type: 'QUANTITY_ADJUSTED',
          quantity: 2,
          reason: 'found on rack',
        }),
      ),
    ),
  ).toBe('Added 2 at Lathe · QF-140 · reason: found on rack · LATHE-ST-1');
});

/** One wire Movement in the client model (the api module's mapping). */
function toModel(wire: ReturnType<typeof movement>) {
  return {
    id: wire.id as number,
    quantityFlowId: wire.quantity_flow_id as number,
    movementType: wire.movement_type as string,
    quantity: wire.quantity as number,
    fromArea: wire.from_area
      ? { ...(wire.from_area as typeof LATHE), isTerminal: false }
      : null,
    toArea: { ...(wire.to_area as typeof LATHE), isTerminal: false },
    operation: { ...OPERATION, isExternal: false },
    sourceMachine: wire.source_machine as null,
    destinationMachine: wire.destination_machine as null,
    stationId: wire.station_id as string | null,
    occurredAt: wire.occurred_at as string,
    deviceEventId: 'evt',
    commandSequence: 1,
    movementReason: wire.movement_reason as string | null,
    reason: wire.reason as string | null,
    reversesMovementId: null,
    reversedByMovementId: null,
    assignedRouteStep: null,
    routeDeviation: null,
    lineage: (
      wire.lineage as {
        parent_flow_id: number;
        child_flow_id: number;
        relation: string;
      }[]
    ).map((edge) => ({
      parentFlowId: edge.parent_flow_id,
      childFlowId: edge.child_flow_id,
      relation: edge.relation,
    })),
    demand: null,
  };
}

/* ==== Detail selection toggle and floating overlay (GUI v14) ==== */

test('clicking the selected PN row again unselects it and closes the overlay', async () => {
  await renderTracking();
  const row = await openFirstRow();
  expect(row.getAttribute('aria-pressed')).toBe('true');
  expect(document.querySelector('.tk-right')).not.toBeNull();

  fireEvent.click(row);
  // Unselected: the floating overlay is gone; the table itself never
  // changed width (single-column layout in both states).
  expect(row.getAttribute('aria-pressed')).toBe('false');
  expect(document.querySelector('.tk-right')).toBeNull();

  // Clicking again re-selects and re-opens the details.
  fireEvent.click(row);
  await act(async () => {});
  expect(row.getAttribute('aria-pressed')).toBe('true');
  expect(document.querySelector('.tk-right')).not.toBeNull();
});

test('the whole result row is the click target — not only the PN text', async () => {
  await renderTracking();

  const firstRow = document.querySelector('.tk-table tbody tr') as HTMLElement;
  const button = firstRow.querySelector('.rowbtn') as HTMLElement;
  expect(firstRow.classList.contains('selrow')).toBe(true);

  // A click on a plain data cell (e.g. the quantity cell) toggles the
  // same selection the keyboard-focusable PN button controls.
  const qtyCell = firstRow.querySelectorAll('td')[3] as HTMLElement;
  fireEvent.click(qtyCell);
  await act(async () => {});
  expect(button.getAttribute('aria-pressed')).toBe('true');
  expect(document.querySelector('.tk-right')).not.toBeNull();

  fireEvent.click(qtyCell);
  expect(button.getAttribute('aria-pressed')).toBe('false');
  expect(document.querySelector('.tk-right')).toBeNull();

  // No nested interactive controls inside a result row besides the one
  // selection button — row clicks can never fight another control.
  expect(firstRow.querySelectorAll('button, a, input, select').length).toBe(1);
});

test('the detail panel closes through its accessible X button and restores focus', async () => {
  await renderTracking();
  const row = await openFirstRow();

  const close = screen.getByRole('button', { name: 'Close details' });
  expect(close.closest('.tk-right')).not.toBeNull();
  fireEvent.click(close);

  expect(document.querySelector('.tk-right')).toBeNull();
  expect(row.getAttribute('aria-pressed')).toBe('false');
  // Focus returns to the originating result row.
  expect(row).toHaveFocus();
});

test('Escape closes the modeless overlay and restores focus to the row', async () => {
  await renderTracking();
  await openFirstRow();

  expect(document.querySelector('.tk-right')).not.toBeNull();
  fireEvent.keyDown(window, { key: 'Escape' });

  expect(document.querySelector('.tk-right')).toBeNull();
  expect(document.querySelector('.tk-table .rowbtn')).toHaveFocus();

  // With nothing selected, Escape is inert.
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(document.querySelector('.tk-right')).toBeNull();
});

test('a click outside every row and outside the panel closes the overlay', async () => {
  await renderTracking();
  const row = await openFirstRow();

  expect(document.querySelector('.tk-right')).not.toBeNull();
  // The search input is inside .tk-left but outside every result row.
  fireEvent.mouseDown(screen.getByLabelText('Search PN, WO, Job Number'));

  expect(document.querySelector('.tk-right')).toBeNull();
  expect(row.getAttribute('aria-pressed')).toBe('false');
  // Unlike Escape and the close button, a plain outside click does not
  // steal focus back to the row.
  expect(row).not.toHaveFocus();

  // With nothing selected, an outside click is inert.
  fireEvent.mouseDown(document.body);
  expect(document.querySelector('.tk-right')).toBeNull();
});

test('a click on the selected row or inside the panel itself does not count as outside', async () => {
  await renderTracking();
  const row = await openFirstRow();
  fireEvent.mouseDown(row);
  expect(document.querySelector('.tk-right')).not.toBeNull();

  const panel = document.querySelector('.tk-right') as HTMLElement;
  fireEvent.mouseDown(panel);
  expect(document.querySelector('.tk-right')).not.toBeNull();
});

test('selecting a different PN switches the panel to that PN with its own read', async () => {
  const fetchMock = stubFetch((url) =>
    url.startsWith('/api/tracking/detail?part_number=142-260')
      ? jsonResponse(
          { detail: 'Part Number 142-260 is not known to PartFlow.' },
          404,
        )
      : defaultAnswer(url),
  );
  await renderTracking();
  await openFirstRow();

  const rows = document.querySelectorAll('.tk-table .rowbtn');
  expect(rows.length).toBe(2);
  fireEvent.click(rows[1]);
  await act(async () => {});

  expect(rows[1].getAttribute('aria-pressed')).toBe('true');
  expect(rows[0].getAttribute('aria-pressed')).toBe('false');
  expect(trackingCalls(fetchMock).at(-1)).toBe(
    '/api/tracking/detail?part_number=142-260&movements_limit=50',
  );
  // The panel stays (with this PN's own state) and still offers the
  // close control.
  const panel = document.querySelector('.tk-right') as HTMLElement;
  expect(panel.querySelector('h2')?.textContent).toBe('142-260');
  expect(within(panel).getByRole('alert')).toHaveTextContent('not known');
  expect(
    screen.getByRole('button', { name: 'Close details' }),
  ).toBeInTheDocument();
});

test('the overlay panel floats above a fixed-width results layout', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'tracking.css'),
    'utf8',
  );
  // Modeless floating overlay: fixed positioning, own scrolling,
  // elevation — and the wrapper keeps ONE grid column in every state,
  // so opening/closing the panel can never reflow the results table.
  expect(css).toMatch(/\.tk-right \{[^}]*position: fixed/);
  expect(css).toMatch(/\.tk-right \{[^}]*overflow: auto/);
  expect(css).toMatch(/\.tk-right \{[^}]*box-shadow: var\(--shadow\)/);
  expect(css).toMatch(
    /\.tk-wrap \{[^}]*grid-template-columns: minmax\(0, 1fr\);/,
  );
  expect(css).not.toContain('noselect');
});

test('the Tracking flow-header RouteModeChip keeps its compact variant', async () => {
  // The DEFAULT RouteModeChip matches the TypeChip metrics
  // (styles/global.css); the Tracking Details panel deliberately keeps
  // the smaller compact chip.
  const { readFileSync } = await import('node:fs');
  const { dirname, join } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'tracking.css'),
    'utf8',
  );
  const compact = /\.qflow \.routechip \{[^}]*}/s.exec(css)![0];
  expect(compact).toContain('font-size: 10.5px');
});
