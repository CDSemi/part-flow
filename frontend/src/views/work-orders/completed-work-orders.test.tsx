import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Completed Work Orders page tests (GUI_DESIGN §11.5; Phase 10). The
// page is a REAL view on `GET /api/work-orders/completed`: completion
// is derived by the SERVER (every demand line fully allocated), a
// completed Work Order leaves the active list and appears in the
// permanent read-only history, and the search (WO Number, PN, Job
// Number), the Done range, the due outcome and the keyset paging are
// server-side — this fake implements that contract over an in-memory
// history and records every request so the parameters can be asserted.
// Also covered: the read-only details with the Done date and the
// allocation figures, the active list excluding completed Work Orders,
// and the New Work Order lookup of a completed number opening its
// read-only details instead of duplicating it.

interface FakeWorkOrder {
  id: number;
  number: string | null;
  received: string;
  due: string | null;
  /** ISO timestamp, set exactly on a completed Work Order. */
  completedAt: string | null;
  lines: {
    id: number;
    pn: string;
    requested: number;
    allocated: number;
    released: number;
    jobs: string[];
  }[];
}

let workOrders: FakeWorkOrder[];
let requests: string[];
/** Server-side calendar verdicts forced per Work Order id — proves the
 * page renders the SERVER's judgement, never its own. */
let outcomeOverrides: Map<
  number,
  { done: string; outcome: 'ON_TIME' | 'LATE'; daysLate: number | null }
>;
/** When set, keyset continuations (requests carrying a cursor) are
 * answered only when released — the response is computed at request
 * time, so it still describes the filters it was asked for. */
let holdContinuations: boolean;
let heldContinuations: (() => void)[];

/** The fake server's site calendar — this device's local date stands
 * in for the site time zone the real backend applies. */
function doneDateOf(w: FakeWorkOrder): string | null {
  return w.completedAt ? localDate(w.completedAt) : null;
}

/** The server-derived due outcome on the site calendar. */
function dueOutcomeOf(w: FakeWorkOrder): {
  outcome: 'ON_TIME' | 'LATE' | 'NO_DUE_DATE' | null;
  daysLate: number | null;
} {
  const done = doneDateOf(w);
  if (done === null) return { outcome: null, daysLate: null };
  if (w.due === null) return { outcome: 'NO_DUE_DATE', daysLate: null };
  if (done > w.due) {
    const days = Math.round(
      (Date.parse(`${done}T00:00:00Z`) - Date.parse(`${w.due}T00:00:00Z`)) /
        86_400_000,
    );
    return { outcome: 'LATE', daysLate: days };
  }
  return { outcome: 'ON_TIME', daysLate: null };
}

function summaryWire(w: FakeWorkOrder) {
  const forced = outcomeOverrides.get(w.id);
  const { outcome, daysLate } = forced
    ? { outcome: forced.outcome, daysLate: forced.daysLate }
    : dueOutcomeOf(w);
  return {
    id: w.id,
    work_order_number: w.number,
    received_date: w.received,
    due_date: w.due,
    status: w.completedAt ? 'COMPLETED' : 'OPEN',
    completed_at: w.completedAt,
    done_date: forced ? forced.done : doneDateOf(w),
    due_outcome: outcome,
    days_late: daysLate,
    demand_line_count: w.lines.length,
    part_numbers: w.lines.map((line) => line.pn),
  };
}

function detailWire(w: FakeWorkOrder) {
  return {
    ...summaryWire(w),
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    demands: w.lines.map((line) => ({
      id: line.id,
      work_order_id: w.id,
      part_number: line.pn,
      request_type: 'NEW',
      requested_quantity: line.requested,
      allocated_quantity: line.allocated,
      due_date: w.due,
      priority_rank: null,
      job_numbers: line.jobs,
      requester: null,
      reason: null,
      notes: null,
      has_released_quantity: line.released > 0,
      released_quantity: line.released,
      remaining_quantity: line.requested - line.released,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    })),
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function localDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** The server's completed history: filtered, sorted (NULLs last in
 * either direction, id as the tie-breaker following the direction),
 * keyset-paged on an opaque cursor bound to the sort — never offset
 * paging. The Done range is inclusive done DATES on the site calendar. */
function completedPage(url: URL) {
  const search = (url.searchParams.get('search') ?? '').toLowerCase();
  const doneFrom = url.searchParams.get('done_from');
  const doneTo = url.searchParams.get('done_to');
  const dueOutcome = url.searchParams.get('due_outcome') ?? 'ALL';
  const sort = url.searchParams.get('sort') ?? 'DONE';
  const direction = url.searchParams.get('direction') ?? 'DESC';
  const limit = Number(url.searchParams.get('limit') ?? '50');
  const cursor = url.searchParams.get('cursor');
  let rows = workOrders.filter((w) => w.completedAt !== null);
  const historyTotal = rows.length;
  if (search) {
    rows = rows.filter(
      (w) =>
        (w.number ?? '').toLowerCase().includes(search) ||
        w.lines.some(
          (line) =>
            line.pn.toLowerCase().includes(search) ||
            line.jobs.some((job) => job.toLowerCase().includes(search)),
        ),
    );
  }
  if (doneFrom) rows = rows.filter((w) => doneDateOf(w)! >= doneFrom);
  if (doneTo) rows = rows.filter((w) => doneDateOf(w)! <= doneTo);
  if (dueOutcome !== 'ALL') {
    rows = rows.filter((w) => dueOutcomeOf(w).outcome === dueOutcome);
  }
  const value = (w: FakeWorkOrder): string | null =>
    sort === 'DONE'
      ? w.completedAt
      : sort === 'RECEIVED'
        ? w.received
        : sort === 'DUE'
          ? w.due
          : w.number;
  const sign = direction === 'ASC' ? 1 : -1;
  rows.sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va === null && vb !== null) return 1;
    if (va !== null && vb === null) return -1;
    if (va !== null && vb !== null && va !== vb) return va < vb ? -sign : sign;
    return (a.id - b.id) * sign;
  });
  const total = rows.length;
  if (cursor) {
    const position = JSON.parse(cursor) as {
      sort: string;
      direction: string;
      value: string | null;
      id: number;
    };
    if (position.sort !== sort || position.direction !== direction) {
      return json(
        { detail: 'The paging cursor belongs to another sort order.' },
        422,
      );
    }
    const index = rows.findIndex((w) => w.id === position.id);
    rows = rows.slice(index + 1);
  }
  const page = rows.slice(0, limit);
  const last = page.length === limit ? page[page.length - 1] : null;
  return json({
    work_orders: page.map(summaryWire),
    total,
    history_total: historyTotal,
    next_cursor: last
      ? JSON.stringify({ sort, direction, value: value(last), id: last.id })
      : null,
  });
}

function handle(rawUrl: string, method: string): Response {
  const url = new URL(rawUrl, 'http://localhost');
  requests.push(`${method} ${url.pathname}${url.search}`);
  if (url.pathname === '/api/health') return json({ status: 'ok' });
  if (url.pathname === '/api/work-orders/completed') {
    return completedPage(url);
  }
  if (url.pathname === '/api/work-orders') {
    const number = url.searchParams.get('number');
    if (number !== null) {
      // The exact resolution reaches the WHOLE history — completed
      // Work Orders included (uniqueness spans the history).
      return json(
        workOrders.filter((w) => w.number === number).map(summaryWire),
      );
    }
    const search = (url.searchParams.get('search') ?? '').toLowerCase();
    // The ACTIVE list: a completed Work Order left it.
    return json(
      workOrders
        .filter((w) => w.completedAt === null)
        .filter(
          (w) => !search || (w.number ?? '').toLowerCase().includes(search),
        )
        .map(summaryWire),
    );
  }
  const detail = /^\/api\/work-orders\/(\d+)$/.exec(url.pathname);
  if (detail) {
    const found = workOrders.find((w) => w.id === Number(detail[1]));
    return found ? json(detailWire(found)) : json({ detail: 'nope' }, 404);
  }
  if (/\/api\/(part-numbers|route-templates)/.test(url.pathname)) {
    return json([]);
  }
  return json({ detail: `Unhandled ${method} ${rawUrl}` }, 500);
}

/** ISO timestamp `days` before today (local noon, so the date is stable). */
function daysAgoIso(days: number): string {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() - days);
  return date.toISOString();
}

function line(
  id: number,
  pn: string,
  requested: number,
  allocated: number,
  released: number,
  jobs: string[] = [],
) {
  return { id, pn, requested, allocated, released, jobs };
}

beforeEach(() => {
  requests = [];
  // One active Work Order, three recently completed ones (on time /
  // late / undated internal), one completed long ago, and 55 generated
  // completions in between so the 50-row page has a second page.
  workOrders = [
    {
      id: 1,
      number: '007201',
      received: '2026-08-01',
      due: null,
      completedAt: null,
      lines: [line(11, 'A-100', 5, 2, 5)],
    },
    {
      id: 2,
      number: '006996',
      received: '2026-08-02',
      due: '2099-01-01',
      completedAt: daysAgoIso(2),
      lines: [
        line(21, '81-1042', 6, 6, 6, ['JOB-18112']),
        line(22, 'B-200', 2, 2, 0),
      ],
    },
    {
      id: 3,
      number: '006990',
      received: '2026-07-01',
      due: '2026-07-10',
      completedAt: daysAgoIso(5),
      lines: [line(31, 'C-300', 4, 4, 4)],
    },
    {
      id: 4,
      number: null,
      received: '2026-07-15',
      due: null,
      completedAt: daysAgoIso(9),
      lines: [line(41, 'D-400', 1, 1, 1)],
    },
    {
      id: 5,
      number: '006721',
      received: '2025-01-05',
      due: '2025-02-01',
      completedAt: daysAgoIso(400),
      lines: [line(51, 'E-500', 3, 3, 3)],
    },
    ...Array.from({ length: 55 }, (_, i): FakeWorkOrder => ({
      id: 100 + i,
      number: `0069${String(i).padStart(2, '0')}`,
      received: '2026-06-01',
      due: '2099-01-01',
      completedAt: daysAgoIso(20 + i),
      lines: [line(1000 + i, `GEN-${i}`, 1, 1, 1)],
    })),
  ];
  holdContinuations = false;
  heldContinuations = [];
  outcomeOverrides = new Map();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const response = handle(String(input), init?.method ?? 'GET');
      if (holdContinuations && String(input).includes('cursor=')) {
        return new Promise<Response>((resolve) => {
          heldContinuations.push(() => resolve(response));
        });
      }
      return Promise.resolve(response);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderCompleted() {
  window.history.replaceState({}, '', '/management/work-orders/completed');
  render(<App />);
  await screen.findByRole('heading', { name: 'Completed Work Orders' });
  await screen.findByText(/completed Work Orders ·/);
}

async function renderActiveList() {
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
}

function visibleRows(): HTMLTableRowElement[] {
  return Array.from(document.querySelectorAll('.cwo-list tbody tr.selrow'));
}

function completedRequests() {
  return requests.filter((r) => r.startsWith('GET /api/work-orders/completed'));
}

/* ============ The history page ============ */

test('the completed page loads the server history newest first within the last 90 days', async () => {
  await renderCompleted();

  // The server received the default range and no search; the page
  // restates the effective range — never mistaken for everything.
  const first = completedRequests()[0];
  expect(first).toContain('done_from=');
  expect(first).not.toContain('search=');
  expect(first).toContain('limit=50');
  expect(document.querySelector('.cwo-summary')).toHaveTextContent(
    'Showing 50 of 58 completed Work Orders · last 90 days',
  );

  // Newest done date first: the server's order is rendered as is.
  const rows = visibleRows();
  expect(rows[0]).toHaveTextContent('006996');
  expect(rows[1]).toHaveTextContent('006990');
  expect(rows[2]).toHaveTextContent('internal Work Order');
  // The due outcome markers are explicit (never color-only).
  expect(rows[0]).toHaveTextContent('✓ On time');
  expect(rows[1]).toHaveTextContent(/✕ \d+ days? late/);
  expect(rows[0]).toHaveTextContent('81-1042');

  // History outside the window was never listed — the old completion
  // is absent — and the page offers no New Work Order entry.
  expect(
    screen.queryByRole('button', { name: 'Open Work Order 006721' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '＋ New Work Order' }),
  ).not.toBeInTheDocument();
});

test('"none ever" is the server\'s whole-history count — the default window alone never hides it', async () => {
  // Nothing ever completed: the plain empty state, although the default
  // 90-day range is in force.
  workOrders = workOrders.filter((w) => w.completedAt === null);
  window.history.replaceState({}, '', '/management/work-orders/completed');
  render(<App />);
  expect(
    await screen.findByText(/No completed Work Orders yet/),
  ).toBeInTheDocument();
  expect(document.querySelector('.cwo-summary')).toBeNull();
  cleanup();

  // History exists but none of it inside the range: "none in this
  // range" with the widening hint — never the "none ever" state.
  workOrders = [
    ...workOrders,
    {
      id: 5,
      number: '006721',
      received: '2025-01-05',
      due: '2025-02-01',
      completedAt: daysAgoIso(400),
      lines: [line(51, 'E-500', 3, 3, 3)],
    },
  ];
  await renderCompleted();
  expect(
    screen.getByText(/No completed Work Orders match in this range/),
  ).toBeInTheDocument();
  expect(screen.queryByText(/No completed Work Orders yet/)).toBeNull();
  expect(document.querySelector('.cwo-summary')).toHaveTextContent(
    'Showing 0 of 0 completed Work Orders · last 90 days',
  );
});

test('search runs on the server over WO Number, PN and Job Number', async () => {
  await renderCompleted();

  fireEvent.change(screen.getByLabelText('Search completed Work Orders'), {
    target: { value: 'JOB-18112' },
  });
  await waitFor(() =>
    expect(completedRequests().at(-1)).toContain('search=JOB-18112'),
  );
  await waitFor(() => expect(visibleRows()).toHaveLength(1));
  expect(visibleRows()[0]).toHaveTextContent('006996');

  fireEvent.change(screen.getByLabelText('Search completed Work Orders'), {
    target: { value: 'c-300' },
  });
  await waitFor(() =>
    expect(completedRequests().at(-1)).toContain('search=c-300'),
  );
  await waitFor(() => expect(visibleRows()[0]).toHaveTextContent('006990'));
  expect(visibleRows()).toHaveLength(1);
});

test('a search miss inside a bounded range offers one-click Search all history', async () => {
  await renderCompleted();

  // 006721 exists — but completed far outside the default 90-day window.
  fireEvent.change(screen.getByLabelText('Search completed Work Orders'), {
    target: { value: '006721' },
  });
  expect(
    await screen.findByText(/No completed Work Orders match in this range/),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Search all history' }));

  // The search text is kept; only the Done range widens — the server
  // request no longer carries a lower bound.
  expect(
    await screen.findByRole('button', { name: 'Open Work Order 006721' }),
  ).toBeInTheDocument();
  expect(completedRequests().at(-1)).not.toContain('done_from=');
  expect(completedRequests().at(-1)).toContain('search=006721');
  expect(screen.getByText(/all time/)).toBeInTheDocument();
});

test('the Due outcome filter and a custom Done range travel to the server', async () => {
  await renderCompleted();

  fireEvent.change(screen.getByLabelText('Due outcome'), {
    target: { value: 'late' },
  });
  await waitFor(() =>
    expect(completedRequests().at(-1)).toContain('due_outcome=LATE'),
  );
  await waitFor(() => expect(visibleRows()).toHaveLength(1));
  expect(visibleRows()[0]).toHaveTextContent('006990');
  expect(visibleRows()[0]).toHaveTextContent(/✕ \d+ days? late/);

  fireEvent.change(screen.getByLabelText('Due outcome'), {
    target: { value: 'nodue' },
  });
  await waitFor(() =>
    expect(completedRequests().at(-1)).toContain('due_outcome=NO_DUE_DATE'),
  );
  await waitFor(() => expect(visibleRows()).toHaveLength(1));
  expect(visibleRows()[0]).toHaveTextContent('internal Work Order');

  // A custom range: both bounds travel as inclusive done DATES the
  // server judges on the site calendar — never local instants.
  fireEvent.change(screen.getByLabelText('Due outcome'), {
    target: { value: 'all' },
  });
  fireEvent.change(screen.getByLabelText('Done date range'), {
    target: { value: 'custom' },
  });
  fireEvent.change(screen.getByLabelText('Done from'), {
    target: { value: localDate(daysAgoIso(6)) },
  });
  fireEvent.change(screen.getByLabelText('Done to'), {
    target: { value: localDate(daysAgoIso(1)) },
  });
  await waitFor(() => {
    const last = completedRequests().at(-1)!;
    expect(last).toContain(`done_from=${localDate(daysAgoIso(6))}`);
    expect(last).toContain(`done_to=${localDate(daysAgoIso(1))}`);
    expect(last).not.toContain('due_outcome=');
  });
  await waitFor(() => expect(visibleRows()).toHaveLength(2));
  expect(visibleRows()[0]).toHaveTextContent('006996');
  expect(visibleRows()[1]).toHaveTextContent('006990');
});

test('Show more continues the server keyset page — never an offset', async () => {
  await renderCompleted();

  fireEvent.change(screen.getByLabelText('Done date range'), {
    target: { value: 'all' },
  });
  await waitFor(() =>
    expect(document.querySelector('.cwo-summary')).toHaveTextContent(
      'Showing 50 of 59 completed Work Orders · all time',
    ),
  );
  expect(visibleRows()).toHaveLength(50);

  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  await waitFor(() => expect(visibleRows()).toHaveLength(59));
  const continuation = completedRequests().at(-1)!;
  expect(continuation).toContain('cursor=');
  expect(document.querySelector('.cwo-summary')).toHaveTextContent(
    'Showing 59 of 59 completed Work Orders · all time',
  );
  // The oldest completion arrives last, and nothing more can be paged.
  expect(visibleRows().at(-1)).toHaveTextContent('006721');
  expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
});

test('a continuation still pending when the filters change is ignored — never appended to the new page', async () => {
  await renderCompleted();
  fireEvent.change(screen.getByLabelText('Done date range'), {
    target: { value: 'all' },
  });
  await waitFor(() =>
    expect(document.querySelector('.cwo-summary')).toHaveTextContent(
      'Showing 50 of 59 completed Work Orders · all time',
    ),
  );

  // Page 2 of the all-time query is requested but the answer is slow…
  holdContinuations = true;
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  await waitFor(() => expect(heldContinuations).toHaveLength(1));
  expect(completedRequests().at(-1)).toContain('cursor=');

  // …and meanwhile the operator narrows the Done range: page 1 of the
  // 90-day query replaces the list.
  fireEvent.change(screen.getByLabelText('Done date range'), {
    target: { value: '90d' },
  });
  await waitFor(() =>
    expect(document.querySelector('.cwo-summary')).toHaveTextContent(
      'Showing 50 of 58 completed Work Orders · last 90 days',
    ),
  );
  expect(visibleRows()).toHaveLength(50);
  const firstPageRequests = completedRequests().length;

  // The stale all-time page 2 arrives now: rows, total and cursor are
  // dropped — the 90-day page is untouched (006721 completed 400 days
  // ago and belongs only to the all-time query).
  await act(async () => {
    heldContinuations.forEach((release) => release());
    await Promise.resolve();
  });
  expect(visibleRows()).toHaveLength(50);
  expect(document.querySelector('.cwo-summary')).toHaveTextContent(
    'Showing 50 of 58 completed Work Orders · last 90 days',
  );
  expect(screen.queryByText('006721')).toBeNull();
  expect(completedRequests()).toHaveLength(firstPageRequests);

  // `Show more` is available again for the CURRENT query and continues
  // from its own cursor within the 90-day range.
  holdContinuations = false;
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  await waitFor(() => expect(visibleRows()).toHaveLength(58));
  const continuation = completedRequests().at(-1)!;
  expect(continuation).toContain('cursor=');
  expect(continuation).toContain('done_from=');
  expect(document.querySelector('.cwo-summary')).toHaveTextContent(
    'Showing 58 of 58 completed Work Orders · last 90 days',
  );
  expect(screen.queryByText('006721')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
});

test('the date and identity columns sort on the server — Done descending by default, the cycle returning to it, paging reset per sort', async () => {
  await renderCompleted();
  // The table re-mounts around every server load: query the header
  // afresh each time.
  const doneHeader = () =>
    screen.getByRole('button', { name: 'Sort by Done' }).closest('th');
  expect(doneHeader()).toHaveAttribute('aria-sort', 'descending');
  expect(completedRequests()[0]).toContain('sort=DONE');
  expect(completedRequests()[0]).toContain('direction=DESC');

  // Sort by Due: ascending first — a fresh first page for that order
  // (no cursor), rendered in the SERVER's order: dated first (earliest
  // due, then by id), undated last.
  fireEvent.change(screen.getByLabelText('Done date range'), {
    target: { value: 'all' },
  });
  await screen.findByText(/all time/);
  const before = completedRequests().length;
  fireEvent.click(screen.getByRole('button', { name: 'Sort by Due' }));
  await waitFor(() =>
    expect(completedRequests().length).toBeGreaterThan(before),
  );
  const ascending = completedRequests().at(-1)!;
  expect(ascending).toContain('sort=DUE');
  expect(ascending).toContain('direction=ASC');
  expect(ascending).not.toContain('cursor=');
  await waitFor(() => expect(visibleRows()[0]).toHaveTextContent('006721'));
  expect(visibleRows()[1]).toHaveTextContent('006990');
  // Equal due dates (2099-01-01) follow the id tie-breaker: id 2 first.
  expect(visibleRows()[2]).toHaveTextContent('006996');
  expect(
    screen.getByRole('button', { name: 'Sort by Due' }).closest('th'),
  ).toHaveAttribute('aria-sort', 'ascending');
  expect(doneHeader()?.getAttribute('aria-sort')).toBeNull();

  // Show more continues the SAME order from the server's cursor.
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  await waitFor(() => expect(visibleRows()).toHaveLength(59));
  const continuation = completedRequests().at(-1)!;
  expect(continuation).toContain('sort=DUE');
  expect(continuation).toContain('cursor=');
  // The undated rows close the ascending order (NULLs last).
  expect(visibleRows().at(-1)).toHaveTextContent('internal Work Order');
  expect(visibleRows().at(-2)).toHaveTextContent('006954');

  // Descending: the undated rows stay last; the loaded pages reset.
  fireEvent.click(screen.getByRole('button', { name: 'Sort by Due' }));
  await waitFor(() =>
    expect(completedRequests().at(-1)).toContain('direction=DESC'),
  );
  await waitFor(() => expect(visibleRows()).toHaveLength(50));
  // Descending id tie-breaker among the equal due dates: id 154 first.
  expect(visibleRows()[0]).toHaveTextContent('006954');
  expect(
    screen.getByRole('button', { name: 'Sort by Due' }).closest('th'),
  ).toHaveAttribute('aria-sort', 'descending');

  // The third click returns to the default order — Done descending.
  fireEvent.click(screen.getByRole('button', { name: 'Sort by Due' }));
  await waitFor(() => {
    const last = completedRequests().at(-1)!;
    expect(last).toContain('sort=DONE');
    expect(last).toContain('direction=DESC');
  });
  await waitFor(() => expect(visibleRows()[0]).toHaveTextContent('006996'));
  expect(doneHeader()).toHaveAttribute('aria-sort', 'descending');

  // WO Number sorts on the server too; the page never re-sorts rows.
  fireEvent.click(screen.getByRole('button', { name: 'Sort by WO Number' }));
  await waitFor(() =>
    expect(completedRequests().at(-1)).toContain('sort=NUMBER'),
  );
  await waitFor(() => expect(visibleRows()[0]).toHaveTextContent('006721'));
  expect(visibleRows()[1]).toHaveTextContent('006900');
});

test("the Done date and the due outcome are the server's verdict on the site calendar — never recomputed from the browser clock", async () => {
  // The server judged this completion on ITS calendar: a local date
  // would say "on time" (due 2099), the site said late by 3 days.
  outcomeOverrides.set(2, {
    done: '2099-01-04',
    outcome: 'LATE',
    daysLate: 3,
  });
  await renderCompleted();
  const row = visibleRows()[0];
  expect(row).toHaveTextContent('006996');
  expect(row).toHaveTextContent('Jan 04, 2099');
  expect(row).toHaveTextContent('✕ 3 days late');

  // The read-only details carry the same server date.
  fireEvent.click(
    screen.getByRole('button', { name: 'Open Work Order 006996' }),
  );
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('Done Jan 04, 2099');
});

test('a row opens the read-only Work Order Details with the Done date and the allocation', async () => {
  await renderCompleted();

  fireEvent.click(
    screen.getByRole('button', { name: 'Open Work Order 006996' }),
  );

  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('81-1042');
  expect(dialog).toHaveTextContent('006996');
  expect(dialog).toHaveTextContent('Completed');
  // The meta line carries the Done date (§11.5) and every line shows
  // its server-owned allocation.
  expect(dialog).toHaveTextContent(/· Done/);
  expect(dialog).toHaveTextContent('Allocated 6/6');
  expect(dialog).toHaveTextContent('Allocated 2/2');
  // Read-only: no Save demand, no release, no removal, no Add Part.
  expect(screen.queryByRole('button', { name: 'Save demand' })).toBeNull();
  expect(
    screen.queryByRole('button', { name: /Release to production/ }),
  ).toBeNull();
  expect(screen.queryByRole('button', { name: /Add Part/ })).toBeNull();
  expect(dialog).toHaveTextContent('read-only history');

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

/* ============ Entry points on the active list ============ */

test('a completed Work Order leaves the active list and the toolbar links to the history', async () => {
  await renderActiveList();

  expect(await screen.findByText('007201')).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Open Work Order 006996' }),
  ).not.toBeInTheDocument();
  expect(document.querySelector('.wostat.completed')).toBeNull();

  const link = screen.getByRole('link', { name: 'Completed Work Orders ›' });
  expect(link.closest('.wo-tools')).not.toBeNull();
  fireEvent.click(link);
  expect(window.location.pathname).toBe('/management/work-orders/completed');
  expect(
    await screen.findByRole('heading', { name: 'Completed Work Orders' }),
  ).toBeInTheDocument();
  expect(
    await screen.findByRole('button', { name: 'Open Work Order 006996' }),
  ).toBeInTheDocument();
});

test('an active-list search miss points at the completed history', async () => {
  await renderActiveList();

  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: '006996' },
  });
  await waitFor(() =>
    expect(
      screen.getByText(/No active Work Order matches/),
    ).toBeInTheDocument(),
  );
  const cell = screen
    .getByText(/No active Work Order matches/)
    .closest('td') as HTMLTableCellElement;
  expect(
    Array.from(cell.querySelectorAll('a')).find(
      (a) => a.textContent === 'Completed Work Orders',
    ),
  ).toBeDefined();
});

test('entering a completed WO Number in New Work Order opens its read-only details — never a duplicate', async () => {
  await renderActiveList();
  fireEvent.click(screen.getByRole('button', { name: '＋ New Work Order' }));
  await screen.findByRole('dialog', { name: 'New Work Order' });

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '006996' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('81-1042');
  expect(dialog).toHaveTextContent('006996');
  expect(dialog).toHaveTextContent('Completed');
  expect(dialog).toHaveTextContent('read-only history');
  expect(screen.queryByRole('button', { name: 'Save demand' })).toBeNull();
  expect(
    screen.getByText(
      /006996 already exists and is completed — opening its read-only details/,
    ),
  ).toBeInTheDocument();
  // Nothing was created: the exact resolution reached the history.
  expect(requests.filter((r) => r.startsWith('POST'))).toHaveLength(0);
  expect(requests).toContain('GET /api/work-orders?number=006996');
});
