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
import { AREA_BOARD_REFRESH_MS } from './area-board-feed';
import { AreaBoardView } from './AreaBoardView';

// Area Board regressions on the REAL feed (Phase 11): the tab strip and
// the two modes built from the server's Areas, the shared Area/Machine
// monitoring model (summary card + one card per Machine, quantity
// -preserving grouping, finished quantity naming its completing
// Machine), the monitoring context of the shared PN row (due date, Job
// Numbers, Hot rank, time in Area, scrapped), the terminal Stockroom's
// stocked lines, search and the four sort orders, the narrow paged
// presentation, and the feed states (loading / error + Retry / empty /
// stale refresh).

// ---------------------------------------------------------------------------
// A fake `GET /api/area-board` answer (the backend wire shape)
// ---------------------------------------------------------------------------

const minutesAgoIso = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

const isoDateIn = (days: number) => {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
};

type State = 'QUEUED' | 'ON_MACHINE' | 'PROCESSING' | 'READY_TO_TRANSFER';

interface FlowSpec {
  id: number;
  pn: string;
  qty: number;
  state: State;
  machineId?: number;
  /** The Machine that completed finished quantity — a reference, so a
   * retired Machine (no card) still names the completion. */
  completedMachine?: { id: number; name: string };
  minutes: number;
  /** The demand the quantity ORIGINATED from (provenance). */
  wo?: string | null;
  external?: boolean;
}

/** One OPEN demand of a PN — the monitoring context of its rows. */
interface DemandSpec {
  id: number;
  pn: string;
  wo: string | null;
  jobs?: string[];
  dueInDays?: number | null;
  hotRank?: number | null;
  requested?: number;
  requestType?: 'NEW' | 'MODIFY';
}

function demandContextWire(demands: DemandSpec[]) {
  const byPn = new Map<string, DemandSpec[]>();
  for (const demand of demands) {
    byPn.set(demand.pn, [...(byPn.get(demand.pn) ?? []), demand]);
  }
  return [...byPn].map(([pn, group]) => ({
    part_number: pn,
    demands: group.map((demand) => ({
      work_order_id: demand.id,
      work_order_number: demand.wo,
      work_order_demand_id: demand.id,
      request_type: demand.requestType ?? 'NEW',
      requested_quantity: demand.requested ?? 10,
      job_numbers: demand.jobs ?? [],
      due_date:
        demand.dueInDays === undefined || demand.dueInDays === null
          ? null
          : isoDateIn(demand.dueInDays),
      priority_rank: demand.hotRank ?? null,
      received_date: '2026-07-12',
    })),
  }));
}

function flowWire(flow: FlowSpec) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: {
      id: flow.external ? 9 : 1,
      code: flow.external ? 'PLAT' : 'TURN',
      name: flow.external ? 'Plating' : 'Turning',
      is_external: Boolean(flow.external),
      is_active: true,
    },
    processing_state: flow.state,
    machine_id: flow.machineId ?? null,
    completed_machine: flow.completedMachine ?? null,
    entered_at: minutesAgoIso(flow.minutes),
    available_actions: ['TRANSFER', 'SCRAP'],
    work_order:
      flow.wo === undefined
        ? null
        : {
            work_order_id: flow.id,
            work_order_number: flow.wo,
            work_order_demand_id: flow.id,
            request_type: 'NEW',
          },
  };
}

function linesWire(flows: FlowSpec[]) {
  const byPn = new Map<string, FlowSpec[]>();
  for (const flow of flows) {
    byPn.set(flow.pn, [...(byPn.get(flow.pn) ?? []), flow]);
  }
  return [...byPn].map(([pn, group]) => ({
    part_number: pn,
    total_quantity: group.reduce((sum, flow) => sum + flow.qty, 0),
    flows: group.map(flowWire),
  }));
}

function machineWire(
  id: number,
  name: string,
  state: 'RUNNING' | 'IDLE' | 'MAINTENANCE',
  maintenance?: { note: string; expected: string },
) {
  return {
    id,
    name,
    asset_tag: `PB-${1000 + id}`,
    barcode_value: `PF:MACHINE:PB-${1000 + id}`,
    operational_state: state,
    state_changed_at: minutesAgoIso(84),
    maintenance_since: maintenance ? minutesAgoIso(300) : null,
    maintenance_note: maintenance?.note ?? null,
    maintenance_expected_return: maintenance?.expected ?? null,
  };
}

function inventoryWire(
  area: {
    id: number;
    name: string;
    color: string;
    description: string;
    is_terminal?: boolean;
  },
  flows: FlowSpec[],
  machines: ReturnType<typeof machineWire>[],
  demands: DemandSpec[] = [],
) {
  const of = (state: State) => flows.filter((flow) => flow.state === state);
  const total = (state: State) =>
    of(state).reduce((sum, flow) => sum + flow.qty, 0);
  return {
    area: {
      id: area.id,
      name: area.name,
      color: area.color,
      description: area.description,
      is_terminal: area.is_terminal ?? false,
    },
    demand_context: demandContextWire(demands),
    has_machines: machines.length > 0,
    lines: linesWire(flows),
    total_part_numbers: linesWire(flows).length,
    total_quantity: flows.reduce((sum, flow) => sum + flow.qty, 0),
    queued: linesWire(of('QUEUED')),
    queued_quantity: total('QUEUED'),
    machines: machines.map((machine) => {
      const held = flows.filter(
        (flow) => flow.state === 'ON_MACHINE' && flow.machineId === machine.id,
      );
      return {
        machine,
        lines: linesWire(held),
        total_quantity: held.reduce((sum, flow) => sum + flow.qty, 0),
      };
    }),
    on_machine_quantity: total('ON_MACHINE'),
    processing: linesWire(of('PROCESSING')),
    processing_quantity: total('PROCESSING'),
    finished: linesWire(of('READY_TO_TRANSFER')),
    finished_quantity: total('READY_TO_TRANSFER'),
  };
}

const OPERATION = {
  id: 1,
  code: 'TURN',
  name: 'Turning',
  is_external: false,
};

// Lathe: 4 Machines (one idle, one under maintenance), 3 PNs, 12 pcs =
// 4 queued + 7 on Machines + 1 finished (completed by Lathe 3). The Hot
// PN holds THREE separate quantities — one row in the PN-centric
// overview, three rows in the per-Area detail.
const LATHE_FLOWS: FlowSpec[] = [
  {
    id: 1,
    pn: '2027-60-8114-00',
    qty: 3,
    state: 'ON_MACHINE',
    machineId: 3,
    minutes: 125,
    wo: '007001',
  },
  {
    id: 2,
    pn: '2027-60-8114-00',
    qty: 2,
    state: 'QUEUED',
    minutes: 125,
    wo: '007001',
  },
  {
    id: 3,
    pn: '2027-60-8114-00',
    qty: 1,
    state: 'READY_TO_TRANSFER',
    // Completed on a Machine that has since been RETIRED: it is no
    // longer one of the Area's cards, and the row must still say so.
    completedMachine: { id: 9, name: 'Lathe 9' },
    minutes: 40,
    wo: '007001',
  },
  {
    id: 4,
    pn: '0455-20-0118-03',
    qty: 4,
    state: 'ON_MACHINE',
    machineId: 2,
    minutes: 65,
    wo: '007003',
  },
  {
    id: 5,
    pn: '214-406',
    qty: 2,
    state: 'QUEUED',
    minutes: 372,
    // Quantity released by a Work Order that has since completed: the
    // provenance stays, the monitoring context comes from elsewhere.
    wo: '006990',
  },
];

const LATHE_DEMANDS: DemandSpec[] = [
  {
    id: 1,
    pn: '2027-60-8114-00',
    wo: '007001',
    jobs: ['18112'],
    dueInDays: 2,
    hotRank: 1,
    requested: 6,
  },
  {
    // A SECOND open demand for the Hot PN: the row names the defining
    // one and states there are more — it never picks silently.
    id: 11,
    pn: '2027-60-8114-00',
    wo: '007050',
    jobs: ['18999'],
    dueInDays: 20,
    requested: 4,
  },
  { id: 4, pn: '0455-20-0118-03', wo: '007003', jobs: ['18190'], dueInDays: 9 },
  {
    // An internal MODIFY demand without an external number: the blank
    // number displays as `—`.
    id: 5,
    pn: '214-406',
    wo: null,
    dueInDays: -1,
    requestType: 'MODIFY',
  },
];

const DEBURR_FLOWS: FlowSpec[] = [
  {
    id: 6,
    pn: '81-1042',
    qty: 6,
    state: 'PROCESSING',
    minutes: 140,
    wo: '007021',
  },
  {
    id: 7,
    pn: '78-04-0031',
    qty: 3,
    state: 'READY_TO_TRANSFER',
    minutes: 30,
    wo: '007002',
  },
];

const DEBURR_DEMANDS: DemandSpec[] = [
  { id: 6, pn: '81-1042', wo: '007021', jobs: ['18615'], dueInDays: 7 },
  { id: 7, pn: '78-04-0031', wo: '007002', jobs: ['18102'], dueInDays: 16 },
];

function boardPayload() {
  return {
    department: { id: 1, name: 'Machining' },
    areas: [
      {
        inventory: inventoryWire(
          {
            id: 5,
            name: 'Deburr',
            color: 'var(--a-deburr)',
            description: 'Manual finishing',
          },
          DEBURR_FLOWS,
          [],
          DEBURR_DEMANDS,
        ),
        operations: [{ ...OPERATION, id: 5, code: 'DEB', name: 'Deburring' }],
        scrapped: [],
        stocked: [],
      },
      {
        inventory: inventoryWire(
          {
            id: 2,
            name: 'Lathe',
            color: 'var(--a-lathe)',
            description: 'Turning cell',
          },
          LATHE_FLOWS,
          [
            machineWire(1, 'Lathe 1', 'IDLE'),
            machineWire(2, 'Lathe 2', 'RUNNING'),
            machineWire(3, 'Lathe 3', 'RUNNING'),
            machineWire(4, 'Lathe 4', 'MAINTENANCE', {
              note: 'Spindle bearing replacement',
              expected: '2026-08-06',
            }),
          ],
          LATHE_DEMANDS,
        ),
        operations: [OPERATION],
        scrapped: [
          { part_number: '2027-60-8114-00', quantity: 1 },
          { part_number: '214-406', quantity: 1 },
        ],
        stocked: [],
      },
      {
        inventory: inventoryWire(
          {
            id: 8,
            name: 'Stockroom',
            color: 'var(--a-stockroom)',
            description: 'Finished goods',
            is_terminal: true,
          },
          [],
          [],
        ),
        operations: [{ ...OPERATION, id: 9, code: 'STK', name: 'Stocking' }],
        scrapped: [],
        stocked: [
          { part_number: '309-127', quantity: 50, allocated_quantity: 50 },
        ],
      },
    ],
  };
}

function stubFetch(
  answer: (url: string) => Response | Promise<Response> = () =>
    new Response(JSON.stringify(boardPayload()), { status: 200 }),
) {
  const fetchMock = vi.fn((input: RequestInfo | URL) =>
    Promise.resolve(answer(String(input))),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Render the view with a healthy shared connectivity state. */
async function renderBoard() {
  const result = render(
    <ConnectivityContext.Provider
      value={{ status: 'connected', retry: () => {} }}
    >
      <AreaBoardView />
    </ConnectivityContext.Provider>,
  );
  // The first feed answer resolves in a microtask.
  await act(async () => {});
  return result;
}

/**
 * Simulate the phone/tablet media state: the paged All Areas
 * presentation reads ONE media query (the §2.5 collapse point) via
 * matchMedia — jsdom applies no real media queries.
 */
function stubNarrowViewport(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches,
      media: '',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

function openArea(name: RegExp) {
  // Click the Area TAB specifically — the overview column headers are
  // buttons too and would make an unscoped name query ambiguous.
  const tabs = document.querySelector<HTMLElement>('.ab-tabs')!;
  fireEvent.click(within(tabs).getByRole('button', { name }));
}

beforeEach(() => {
  window.history.replaceState({}, '', '/management/area-board');
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// The feed
// ---------------------------------------------------------------------------

test('the board reads its Department from the server feed', async () => {
  const fetchMock = stubFetch();
  await renderBoard();

  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/area-board');
  // One tab per Area, in the server's order, with its own item count.
  const tabs = Array.from(
    document.querySelectorAll('.ab-tabs .ab-tab'),
    (tab) => tab.textContent,
  );
  expect(tabs[0]).toContain('All Areas');
  expect(tabs[1]).toContain('Deburr');
  expect(tabs[2]).toContain('Lathe');
  expect(tabs[3]).toContain('Stockroom');
  // The feed is live while a complete board is on screen.
  expect(document.querySelector('.ab-feed')?.textContent).toBe('Live');
  expect(document.querySelector('.ab-feed')?.classList.contains('stale')).toBe(
    false,
  );
});

test('`?department=` addresses one Department', async () => {
  window.history.replaceState({}, '', '/management/area-board?department=7');
  const fetchMock = stubFetch();
  await renderBoard();

  expect(String(fetchMock.mock.calls[0][0])).toBe(
    '/api/area-board?department_id=7',
  );
});

test('a failed first load is the error state with Retry', async () => {
  let attempt = 0;
  stubFetch(() => {
    attempt += 1;
    return attempt === 1
      ? new Response(
          JSON.stringify({ detail: 'Department 9 does not exist.' }),
          {
            status: 404,
          },
        )
      : new Response(JSON.stringify(boardPayload()), { status: 200 });
  });
  await renderBoard();

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Area Board data could not be loaded.',
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Department 9 does not exist.',
  );

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(document.querySelector('.ab-tabs')).not.toBeNull();
});

test('a failed refresh keeps the last complete board and marks the feed stale', async () => {
  vi.useFakeTimers();
  let attempt = 0;
  stubFetch(() => {
    attempt += 1;
    return attempt === 1
      ? new Response(JSON.stringify(boardPayload()), { status: 200 })
      : Promise.reject(new Error('offline'));
  });
  render(
    <ConnectivityContext.Provider
      value={{ status: 'connected', retry: () => {} }}
    >
      <AreaBoardView />
    </ConnectivityContext.Provider>,
  );
  await act(async () => {});
  expect(document.querySelector('.ab-feed')?.textContent).toBe('Live');

  await act(async () => {
    vi.advanceTimersByTime(AREA_BOARD_REFRESH_MS + 10);
  });
  await act(async () => {});

  // The last complete board stays on screen; only the status changes.
  expect(document.querySelector('.ab-tabs')).not.toBeNull();
  const feed = document.querySelector('.ab-feed')!;
  expect(feed.classList.contains('stale')).toBe(true);
  expect(feed.textContent).toBe('Feed stale — reconnecting');
});

test('a Department without active Areas is an explicit empty state', async () => {
  stubFetch(
    () =>
      new Response(
        JSON.stringify({ department: { id: 1, name: 'Machining' }, areas: [] }),
        { status: 200 },
      ),
  );
  await renderBoard();

  expect(
    screen.getByText('No active Areas are configured in this Department.'),
  ).toBeInTheDocument();
  expect(document.querySelector('.ab-tabs')).toBeNull();
});

// ---------------------------------------------------------------------------
// Per-Area detail — the shared monitoring layout
// ---------------------------------------------------------------------------

test('the Area detail leads with a summary card: stats + grouped PN list', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  const cards = Array.from(document.querySelectorAll('.abd-card'));
  expect(cards.length).toBe(5); // 1 Area summary + 4 Lathe machines
  const summary = cards[0];
  expect(summary.className).toContain('abd-summary');
  expect(summary.textContent).toContain('Turning');

  // Statistics reconcile: 3 PNs · 12 pcs = 4 queued + 7 on machines +
  // 1 done (READY_TO_TRANSFER) · 1 Hot.
  const stats = Array.from(
    summary.querySelectorAll('.stat .n'),
    (el) => el.textContent,
  );
  expect(stats).toEqual(['3', '12', '4', '7', '1', '1']);

  // Grouping: On Machines, then the Area queue, then the finished
  // (ready to transfer) quantity.
  const groups = Array.from(
    summary.querySelectorAll('.abd-grp'),
    (el) => el.textContent,
  );
  expect(groups).toEqual([
    'On Machines',
    'Area queue — awaiting Machine',
    'Finished — ready to move',
  ]);

  // Quantities are neither duplicated nor lost by the grouping.
  const quantities = Array.from(summary.querySelectorAll('.mc-list .q'), (el) =>
    Number(el.textContent),
  );
  expect(quantities.reduce((s, q) => s + q, 0)).toBe(12);
});

test('each Machine gets a monitoring card; inactive Machines stay distinct', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  const machineCards = Array.from(document.querySelectorAll('.abd-machine'));
  expect(
    machineCards.map((card) => card.querySelector('.mname')?.textContent),
  ).toEqual(['Lathe 1', 'Lathe 2', 'Lathe 3', 'Lathe 4']);

  // The server's derived operational state, with the time in that state
  // derived from the shared stateChangedAt timestamp.
  const lathe3 = machineCards[2];
  expect(lathe3.querySelector('.mstat')?.textContent).toMatch(
    /^running · \d+[a-z]/,
  );
  expect(lathe3.textContent).toContain('2027-60-8114-00');
  // Only the actively assigned 3 pcs — the finished 1 pc left the card.
  // The totals use two SEMANTIC value classes (never positional
  // selectors); both keep the secondary muted neutral of the totals
  // line, quieter than the header Total PNs (primary text neutral).
  expect(lathe3.querySelector('.mtotals .machine-total-pcs')?.textContent).toBe(
    '3',
  );
  expect(lathe3.querySelector('.mtotals .machine-total-pns')?.textContent).toBe(
    '1',
  );

  const lathe1 = machineCards[0];
  expect(lathe1.querySelector('.mstat')?.textContent).toMatch(/^idle · /);
  expect(lathe1.querySelector('.mstat .mage')).not.toBeNull();
  expect(lathe1.textContent).toContain('No production assigned');

  const lathe4 = machineCards[3];
  expect(lathe4.className).toContain('maintenance');
  expect(lathe4.querySelector('.mstat')?.textContent).toMatch(
    /^maintenance · /,
  );
  // Maintenance context from the server: note + expected return date.
  expect(lathe4.textContent).toContain(
    'Under maintenance — accepts no production',
  );
  expect(lathe4.textContent).toContain('Spindle bearing replacement');
  expect(lathe4.textContent).toContain('expected back 2026-08-06');
});

test('Areas without Machines render only the summary card — no placeholders', async () => {
  await renderBoard();
  openArea(/^Deburr/);

  const layout = document.querySelector('.am');
  expect(layout?.classList.contains('am-single')).toBe(true);
  expect(document.querySelectorAll('.abd-card').length).toBe(1);
  expect(document.querySelector('.abd-machine')).toBeNull();
  // Direct processing renders its two groups — actively processing
  // quantity under `In processing`, finished (READY_TO_TRANSFER)
  // quantity under `Finished — ready to move` — with no queue wording
  // for a no-Machine Area, and never presented as Stocked.
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('In processing');
  expect(summary?.textContent).toContain('Finished — ready to move');
  expect(summary?.textContent).not.toContain('awaiting Machine');
  expect(summary?.textContent).not.toContain('Stocked');
  // The Area Board stays completely read-only: the Scan Station's
  // direct-processing DONE row action never renders here — no action
  // rail cells at all.
  expect(
    screen.queryByRole('button', { name: 'Complete Area processing' }),
  ).toBeNull();
  expect(document.querySelector('.mc-list .actcell')).toBeNull();
});

test('the detail uses the shared [summary | machine grid] layout, read-only', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  const layout = document.querySelector('.am');
  expect(layout).not.toBeNull();
  expect(layout?.classList.contains('am-single')).toBe(false);
  expect(document.querySelectorAll('.am-machines .abd-machine').length).toBe(4);
  expect(screen.queryByRole('button', { name: 'ASSIGN' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'QUEUE' })).toBeNull();
  expect(document.querySelector('.mc-list .actcell')).toBeNull();
});

test('finished quantity lives in the Area summary, naming its completing Machine', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary')!;
  expect(summary.querySelector('.ctx.done')).not.toBeNull();
  // `Lathe 9` completed the work and has since been retired: it is not
  // one of the Area's four cards, and the completion context survives
  // that — the Machine travels with the quantity, not with the cards.
  expect(
    Array.from(
      document.querySelectorAll('.abd-machine .mname'),
      (el) => el.textContent,
    ),
  ).not.toContain('Lathe 9');
  expect(summary.textContent).toContain('Finished at Lathe 9 — ready to move');
  // The finished pc is not presented as Stocked and never inside a
  // Machine card.
  for (const card of document.querySelectorAll('.abd-machine')) {
    expect(card.textContent).not.toContain('ready to move');
  }
  expect(summary.textContent).not.toContain('Stocked');
});

test('the PN row carries the OPEN demand context and the derived time in Area', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary')!;
  const rows = Array.from(summary.querySelectorAll('.mc-list li'));
  const hot = rows.find((row) => row.textContent?.includes('2027-60-8114-00'))!;
  // The DEFINING open demand (canonical order) with its Job Numbers,
  // the derived countdown and the derived `Time in Area` — none of
  // them stored as text by the server.
  const wo = hot.querySelector<HTMLElement>('.r2 .wo')!;
  expect(wo.textContent).toBe('WO 007001 · 18112 · +1 more');
  // The second open demand is never hidden: the tooltip spells every
  // one of them out, in the canonical order.
  expect(wo.getAttribute('title')).toBe(
    'WO 007001 · 18112 · 6 pcs\nWO 007050 · 18999 · 4 pcs',
  );
  expect(hot.querySelector('.r2 .mono:last-child')?.textContent).toMatch(
    /days left|due today/,
  );
  expect(hot.querySelector('.r3 .tia')?.textContent).toMatch(/in Area$/);
  // Hot rank from the defining demand's priority rank.
  expect(hot.querySelector('.hot')).not.toBeNull();
  // Scrapped quantity of the PN in THIS Area, as text only.
  expect(summary.textContent).toContain('1 scrapped');
  expect(summary.textContent).not.toContain('⊘');
  // The blank-number internal Work Order renders as `—`.
  expect(summary.textContent).toContain('WO —');
});

test("the monitoring context is the open demand, never the quantity's origin", async () => {
  // `214-406` was released by Work Order 006990, which has completed;
  // the PN's own open demand is the internal blank-number one.
  await renderBoard();
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary')!;
  const row = Array.from(summary.querySelectorAll('.mc-list li')).find((item) =>
    item.textContent?.includes('214-406'),
  )!;
  expect(row.querySelector('.r2 .wo')?.textContent).toBe('WO — · —');
  expect(row.textContent).not.toContain('006990');
});

test('a PN with no open demand keeps its row without borrowing a context', async () => {
  stubFetch(() => {
    const payload = boardPayload();
    // Drop the Hot PN's demands: its quantity is still in the Area.
    payload.areas[1].inventory.demand_context =
      payload.areas[1].inventory.demand_context.filter(
        (entry: { part_number: string }) =>
          entry.part_number !== '2027-60-8114-00',
      );
    return new Response(JSON.stringify(payload), { status: 200 });
  });
  await renderBoard();
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary')!;
  const row = Array.from(summary.querySelectorAll('.mc-list li')).find((item) =>
    item.textContent?.includes('2027-60-8114-00'),
  )!;
  // The quantity is still shown, with an empty context rather than the
  // completed Work Order it descends from.
  expect(row.querySelector('.r2 .wo')?.textContent).toBe('WO — · —');
  expect(row.querySelector('.r2 .wo')?.getAttribute('title')).toBe(
    'No open Work Order Demand',
  );
  expect(row.querySelector('.hot')).toBeNull();
  expect(row.textContent).toContain('No due date');
});

test('the terminal Stockroom shows stocked quantity with its allocation', async () => {
  await renderBoard();
  openArea(/^Stockroom/);

  const summary = document.querySelector('.abd-summary')!;
  // Stocked quantity is its own group, never queued, processing or done.
  expect(summary.querySelector('.abd-grp')?.textContent).toBe('Stocked');
  expect(summary.textContent).toContain('309-127');
  expect(summary.textContent).toContain('allocated 50/50');
  // The terminal column's statistics are the stocked ones.
  const stats = Array.from(
    summary.querySelectorAll('.stat .l'),
    (el) => el.textContent,
  );
  expect(stats).toEqual(['PNs', 'Stocked pcs', 'Hot']);
  // Stocked quantity has no entry time: no `Time in Area` is invented.
  expect(summary.querySelector('.r3 .tia')).toBeNull();
});

// ---------------------------------------------------------------------------
// Search and sorting
// ---------------------------------------------------------------------------

test('search still narrows the detail PN lists', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  fireEvent.change(screen.getByLabelText('Search PN, WO, Job Number'), {
    target: { value: '0455' },
  });

  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('0455-20-0118-03');
  expect(summary?.textContent).not.toContain('2027-60-8114-00');
  // Machines whose assignments are filtered out show their empty state.
  const machineCards = Array.from(document.querySelectorAll('.abd-machine'));
  expect(machineCards[2].textContent).toContain('No production assigned');
});

test('Sort: Time in Area orders by the elapsed time since the Area entry', async () => {
  await renderBoard();
  fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'tia' } });

  // The overview is PN-centric: one row per PN, ordered by the OLDEST
  // quantity of that PN — 214-406 (6h 12m), then 2027 (2h 05m, its
  // longest-waiting portion), then 0455 (1h 05m).
  const latheColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  );
  expect(latheColumn).toBeDefined();
  const pns = Array.from(
    latheColumn!.querySelectorAll('.mc-list .p'),
    (el) => el.textContent,
  );
  expect(pns).toEqual(['214-406', '2027-60-8114-00', '0455-20-0118-03']);

  // The detail keeps one row per separate quantity — each is acted on
  // separately at the Scan Station — grouped by holding state.
  openArea(/^Lathe/);
  const detail = Array.from(
    document.querySelectorAll('.abd-summary .mc-list .p'),
    (el) => el.textContent,
  );
  expect(detail.length).toBe(5);
  expect(detail.filter((pn) => pn === '2027-60-8114-00').length).toBe(3);
});

test('Sort: Priority puts every Hot rank before every unranked row', async () => {
  // A rank well past any sentinel value a comparator might assume.
  stubFetch(() => {
    const payload = boardPayload();
    const demands = payload.areas[1].inventory.demand_context as {
      part_number: string;
      demands: { priority_rank: number | null }[];
    }[];
    for (const entry of demands) {
      if (entry.part_number === '0455-20-0118-03') {
        entry.demands[0].priority_rank = 12;
      }
    }
    return new Response(JSON.stringify(payload), { status: 200 });
  });
  await renderBoard();
  fireEvent.change(screen.getByLabelText('Sort'), {
    target: { value: 'prio' },
  });

  const latheColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  )!;
  const pns = Array.from(
    latheColumn.querySelectorAll('.mc-list .p'),
    (el) => el.textContent,
  );
  // Hot #1, then Hot #12 — and the unranked PN last, however high the
  // rank number above it climbs.
  expect(pns).toEqual(['2027-60-8114-00', '0455-20-0118-03', '214-406']);
});

test('Sort: Quantity compares the aggregated PN total, not one quantity', async () => {
  // Deburr holds 81-1042 as TWO quantities of 6 (12 together) and
  // 78-04-0031 as one of 10: sorting per quantity would put the 10
  // first, sorting the PN rows puts 12 first.
  stubFetch(() => {
    const payload = boardPayload();
    const deburr = payload.areas[0].inventory as {
      lines: {
        part_number: string;
        total_quantity: number;
        flows: ReturnType<typeof flowWire>[];
      }[];
      total_quantity: number;
    };
    const second = flowWire({
      id: 71,
      pn: '81-1042',
      qty: 6,
      state: 'PROCESSING',
      minutes: 100,
      wo: '007021',
    });
    const line = deburr.lines.find((item) => item.part_number === '81-1042')!;
    line.flows.push(second);
    line.total_quantity = 12;
    const other = deburr.lines.find(
      (item) => item.part_number === '78-04-0031',
    )!;
    other.flows[0].quantity = 10;
    other.total_quantity = 10;
    deburr.total_quantity = 22;
    return new Response(JSON.stringify(payload), { status: 200 });
  });
  await renderBoard();
  fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'qty' } });

  const deburrColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Deburr'),
  )!;
  const rows = Array.from(deburrColumn.querySelectorAll('.mc-list li'));
  expect(
    rows.map((row) => [
      row.querySelector('.p')?.textContent,
      row.querySelector('.r1 .q')?.textContent,
    ]),
  ).toEqual([
    ['81-1042', '12'],
    ['78-04-0031', '10'],
  ]);

  // The detail still sorts the separate quantities themselves, so the
  // single 10 leads the two 6s there.
  openArea(/^Deburr/);
  const detail = Array.from(
    document.querySelectorAll('.abd-summary .mc-list li'),
    (row) => [
      row.querySelector('.p')?.textContent,
      row.querySelector('.r1 .q')?.textContent,
    ],
  );
  expect(detail).toEqual([
    ['81-1042', '6'],
    ['81-1042', '6'],
    ['78-04-0031', '10'],
  ]);
});

test('search reaches every open demand of a PN, not only the defining one', async () => {
  await renderBoard();
  const search = screen.getByLabelText('Search PN, WO, Job Number');

  // `007050` / `18999` belong to the Hot PN's SECOND open demand — the
  // one the row summarizes as `+1 more`.
  fireEvent.change(search, { target: { value: '007050' } });
  let latheColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  )!;
  expect(
    Array.from(
      latheColumn.querySelectorAll('.mc-list .p'),
      (el) => el.textContent,
    ),
  ).toEqual(['2027-60-8114-00']);

  fireEvent.change(search, { target: { value: '18999' } });
  latheColumn = Array.from(document.querySelectorAll('.ms-col')).find((col) =>
    col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  )!;
  expect(
    Array.from(
      latheColumn.querySelectorAll('.mc-list .p'),
      (el) => el.textContent,
    ),
  ).toEqual(['2027-60-8114-00']);

  // The detail narrows on the same demand context.
  openArea(/^Lathe/);
  expect(
    Array.from(
      document.querySelectorAll('.abd-summary .mc-list .p'),
      (el) => el.textContent,
    ),
  ).toEqual(['2027-60-8114-00', '2027-60-8114-00', '2027-60-8114-00']);

  // A Job Number of no open demand still matches nothing.
  fireEvent.change(screen.getByLabelText('Search PN, WO, Job Number'), {
    target: { value: 'NOSUCHJOB' },
  });
  expect(document.querySelector('.abd-summary .mc-list li')).toBeNull();
});

test('Sort: Due date applies canonical order — Hot first', async () => {
  await renderBoard();
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary');
  const pns = Array.from(
    summary!.querySelectorAll('.mc-list .p'),
    (el) => el.textContent,
  );
  expect(pns[0]).toBe('2027-60-8114-00');
});

// ---------------------------------------------------------------------------
// All Areas overview
// ---------------------------------------------------------------------------

test('the All Areas overview is PN-centric and reuses the shared row presentation', async () => {
  await renderBoard();

  const latheColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  )!;
  // ONE row per Part Number, whatever number of separate quantities it
  // holds in the Area: the Hot PN has three.
  const pns = Array.from(
    latheColumn.querySelectorAll('.mc-list .p'),
    (el) => el.textContent,
  );
  // Canonical demand order (the default sort): Hot #1 first, then the
  // overdue internal demand, then the dated one.
  expect(pns).toEqual(['2027-60-8114-00', '214-406', '0455-20-0118-03']);
  expect(new Set(pns).size).toBe(pns.length);

  // Shared row shell: rowmain grid lines with the shared subcomponents.
  const row = latheColumn.querySelector('.mc-list li .rowmain')!;
  expect(row.querySelector('.r1 .r1r .qtyline')?.textContent).toMatch(
    /\d+ pcs/,
  );
  const wo = row.querySelector<HTMLElement>('.r2 .wo');
  expect(wo?.getAttribute('title')).toContain('WO 007001');

  // The PN's whole presence is aggregated into that one row: its three
  // quantities become the portion chips, and the row total is their sum
  // — nothing lost, nothing counted twice.
  const hotRow = latheColumn.querySelectorAll('.mc-list li')[0];
  expect(hotRow.querySelector('.r1 .q')?.textContent).toBe('6');
  const chips = Array.from(
    hotRow.querySelectorAll('.ctxs .ctx'),
    (el) => el.textContent,
  );
  expect(chips).toEqual(['Lathe 3 × 3', 'queue × 2', 'done × 1']);
  // The column's pieces still reconcile with the Area total.
  const total = Array.from(
    latheColumn.querySelectorAll('.mc-list .r1 .q'),
    (el) => Number(el.textContent),
  ).reduce((sum, qty) => sum + qty, 0);
  expect(total).toBe(12);
  // The overview column shows the Done stat with the success tone.
  expect(latheColumn.querySelector('.stat .n.d')?.textContent).toBe('1');
});

test('a Part Number is counted once per Area, never once per quantity', async () => {
  await renderBoard();

  // Tab count, toolbar meta and the column list agree: 3 PNs in Lathe,
  // although the Area holds five separate quantities.
  const tabs = document.querySelector<HTMLElement>('.ab-tabs')!;
  const latheTab = within(tabs).getByRole('button', { name: /^Lathe/ });
  expect(latheTab.querySelector('.cnt')?.textContent?.trim()).toBe('3');
  // Across all Areas: 3 in Lathe, 2 in Deburr, 1 stocked PN.
  expect(document.querySelector('.ab-meta')?.textContent).toContain('6 PN');

  openArea(/^Lathe/);
  expect(document.querySelector('.ab-meta')?.textContent).toContain('3 PN');
  expect(document.querySelector('.ab-meta')?.textContent).toContain('12');
  // The Area summary counts Part Numbers too, while listing every
  // separate quantity as its own row.
  const summary = document.querySelector('.abd-summary')!;
  expect(summary.querySelector('.stat .n')?.textContent).toBe('3');
  expect(summary.querySelectorAll('.mc-list li').length).toBe(5);
});

test('an aggregated overview row keeps a direct remainder beside a named portion', async () => {
  // One PN processing internally AND at an external Operation in the
  // same no-Machine Area: aggregation must keep both pieces.
  stubFetch(() => {
    const payload = boardPayload();
    const deburr = payload.areas[0].inventory as {
      lines: {
        part_number: string;
        total_quantity: number;
        flows: unknown[];
      }[];
      total_quantity: number;
      processing: unknown[];
      processing_quantity: number;
    };
    const external = flowWire({
      id: 70,
      pn: '81-1042',
      qty: 4,
      state: 'PROCESSING',
      minutes: 90,
      wo: '007021',
      external: true,
    });
    const line = deburr.lines.find((item) => item.part_number === '81-1042')!;
    line.flows.push(external);
    line.total_quantity += 4;
    deburr.total_quantity += 4;
    deburr.processing_quantity += 4;
    return new Response(JSON.stringify(payload), { status: 200 });
  });
  await renderBoard();

  const deburrColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Deburr'),
  )!;
  const row = Array.from(deburrColumn.querySelectorAll('.mc-list li')).find(
    (item) => item.textContent?.includes('81-1042'),
  )!;
  expect(row.querySelector('.r1 .q')?.textContent).toBe('10');
  const chips = Array.from(
    row.querySelectorAll('.ctxs .ctx'),
    (el) => el.textContent,
  );
  expect(chips).toEqual(['vendor × 4', 'processing × 6']);
});

test('the All Areas overview wraps columns via the slide toggle', async () => {
  await renderBoard();

  // Default stays the horizontal scroll layout (GUI §6.2).
  expect(document.querySelector('.ms-scroll')!.classList.contains('wrap')).toBe(
    false,
  );

  const toggle = screen.getByRole('switch', { name: 'Wrap columns' });
  expect(toggle.getAttribute('aria-checked')).toBe('false');
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-checked')).toBe('true');
  expect(document.querySelector('.ms-scroll')!.classList.contains('wrap')).toBe(
    true,
  );

  // The switch belongs to the overview only — a detail tab hides it.
  openArea(/^Lathe/);
  expect(screen.queryByRole('switch', { name: 'Wrap columns' })).toBeNull();

  // Returning to All Areas keeps the chosen layout.
  const tabs = document.querySelector<HTMLElement>('.ab-tabs')!;
  fireEvent.click(within(tabs).getByRole('button', { name: /All Areas/ }));
  expect(document.querySelector('.ms-scroll')!.classList.contains('wrap')).toBe(
    true,
  );
});

// ---------------------------------------------------------------------------
// Narrow presentation (post-v18)
// ---------------------------------------------------------------------------

test('narrow screens hide the tab strip and page the per-Area details (Summary off)', async () => {
  stubNarrowViewport(true);
  await renderBoard();

  // No tab strip — the pages ARE the navigation.
  expect(document.querySelector('.ab-tabs')).toBeNull();
  // The Summary switch (default OFF) replaces Wrap columns.
  const summarySwitch = screen.getByRole('switch', { name: 'Summary' });
  expect(summarySwitch.getAttribute('aria-checked')).toBe('false');
  expect(screen.queryByRole('switch', { name: 'Wrap columns' })).toBeNull();

  // Paged details: one per-Area detail (the shared summary-card
  // layout) per page, Area-colored dots + floating edge buttons.
  expect(document.querySelector('.ms-board.paged')).not.toBeNull();
  const pages = Array.from(document.querySelectorAll('.abd-page'));
  expect(pages.length).toBe(3);
  expect(pages[0].querySelector('.abd-summary')?.textContent).toContain(
    'In this Area now — Deburr',
  );
  const dots = Array.from(document.querySelectorAll('.ms-pagedot'));
  expect(dots.length).toBe(3);
  expect(dots[0].getAttribute('aria-label')).toBe('Go to Deburr');
  expect(dots[0].getAttribute('aria-current')).toBe('true');

  // First page: Previous disabled — paging never wraps.
  expect(screen.getByRole('button', { name: 'Previous Area' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Next Area' }));
  expect(dots[1].getAttribute('aria-current')).toBe('true');
  // The meta line follows the current page's Area.
  expect(document.querySelector('.ab-meta')?.textContent).toContain('in Lathe');

  // A dot jumps directly; the last page disables Next.
  fireEvent.click(screen.getByRole('button', { name: 'Go to Stockroom' }));
  expect(dots[2].getAttribute('aria-current')).toBe('true');
  expect(screen.getByRole('button', { name: 'Next Area' })).toBeDisabled();
});

test('Summary on stacks the overview; a card header jumps to that detail page', async () => {
  stubNarrowViewport(true);
  await renderBoard();

  fireEvent.click(screen.getByRole('switch', { name: 'Summary' }));
  // Stacked overview (wrap layout), no pager chrome, still no tabs.
  expect(document.querySelector('.ms-scroll')!.classList.contains('wrap')).toBe(
    true,
  );
  expect(document.querySelector('.ms-board.paged')).toBeNull();
  expect(document.querySelector('.ms-pagedot')).toBeNull();
  expect(document.querySelector('.ab-tabs')).toBeNull();
  expect(document.querySelector('.ab-meta')?.textContent).toContain(
    'across all Areas',
  );

  // Clicking an Area card header returns to the paged details ON that
  // Area's page (the Summary toggle switches off).
  fireEvent.click(screen.getByTitle('Open Lathe detail'));
  expect(
    screen
      .getByRole('switch', { name: 'Summary' })
      .getAttribute('aria-checked'),
  ).toBe('false');
  expect(document.querySelector('.ms-board.paged')).not.toBeNull();
  const dots = Array.from(document.querySelectorAll('.ms-pagedot'));
  expect(dots[1].getAttribute('aria-current')).toBe('true'); // Lathe
  expect(document.querySelector('.ab-meta')?.textContent).toContain('in Lathe');
});

test('wide viewports keep the tab strip and Wrap columns — never the pager', async () => {
  stubNarrowViewport(false);
  await renderBoard();

  expect(document.querySelector('.ab-tabs')).not.toBeNull();
  expect(
    screen.getByRole('switch', { name: 'Wrap columns' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('switch', { name: 'Summary' })).toBeNull();
  expect(document.querySelector('.ms-board.paged')).toBeNull();
  expect(document.querySelector('.abd-page')).toBeNull();
});

test('queued, on-Machine, processing, and finished states stay distinguishable', async () => {
  await renderBoard();
  openArea(/^Lathe/);
  const summary = document.querySelector('.abd-summary')!;
  expect(summary.textContent).toContain('On Machine');
  expect(summary.textContent).toContain('Awaiting Machine');
  expect(summary.textContent).toContain('ready to move');

  openArea(/^Deburr/);
  expect(document.querySelector('.abd-summary')?.textContent).toContain(
    'In processing',
  );
});
