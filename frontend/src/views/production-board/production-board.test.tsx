import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../../app/connectivity-context';
import { ConnectivityProvider } from '../../app/connectivity-provider';
import { RouterProvider } from '../../app/router-provider';
import { ThemeProvider } from '../../app/theme-provider';
import { isoDateIn, minutesAgoIso } from '../../mocks/mock-time';
import { BOARD_REFRESH_MS } from './board-logic';
import { ProductionBoardView } from './ProductionBoardView';

// Production Board regressions: column order and content-driven sizing
// markup, the standard Hot presentation, urgency-only blinking, aligned
// Areas & Quantities markup with the explicit location-state model
// (machine chip / queue / processing / done / stocked), scrap on its
// own line, canonical row ordering with nullable due dates, the live
// clock, height-aware pagination + rotation, and the real feed
// (loading / error / stale / refresh). jsdom has no layout, so sizing
// rules are asserted through classes/attributes/structure — never
// computed pixel widths.

// ---------------------------------------------------------------------------
// A fake `GET /api/production-board` answer (the backend wire shape)
// ---------------------------------------------------------------------------

const AREA = {
  cut: { id: 1, name: 'Cut', color: 'var(--a-cut)', is_terminal: false },
  lathe: { id: 2, name: 'Lathe', color: 'var(--a-lathe)', is_terminal: false },
  mill: { id: 3, name: 'Mill', color: 'var(--a-mill)', is_terminal: false },
  manual: {
    id: 4,
    name: 'Manual',
    color: 'var(--a-manual)',
    is_terminal: false,
  },
  deburr: {
    id: 5,
    name: 'Deburr',
    color: 'var(--a-deburr)',
    is_terminal: false,
  },
  material: {
    id: 6,
    name: 'Material',
    color: 'var(--a-material)',
    is_terminal: false,
  },
  external: {
    id: 7,
    name: 'External',
    color: 'var(--a-external)',
    is_terminal: false,
  },
  stockroom: {
    id: 8,
    name: 'Stockroom',
    color: 'var(--a-stockroom)',
    is_terminal: true,
  },
};

function location(
  area: (typeof AREA)[keyof typeof AREA],
  state: 'MACHINE' | 'QUEUE' | 'PROCESSING' | 'DONE' | 'STOCKED',
  quantity: number,
  minutesAgo: number | null,
  extra: { machine?: { id: number; name: string }; activity?: string } = {},
) {
  return {
    area,
    machine: extra.machine ?? null,
    activity: extra.activity ?? null,
    quantity,
    state,
    since: minutesAgo === null ? null : minutesAgoIso(minutesAgo),
  };
}

function demand(
  id: number,
  workOrderNumber: string | null,
  jobNumbers: string[],
  requestedQuantity: number,
  extra: {
    allocated?: number;
    requestType?: string;
    dueDate?: string | null;
    priorityRank?: number | null;
    completed?: boolean;
  } = {},
) {
  return {
    work_order_id: id,
    work_order_number: workOrderNumber,
    work_order_demand_id: id,
    request_type: extra.requestType ?? 'NEW',
    requested_quantity: requestedQuantity,
    allocated_quantity: extra.allocated ?? 0,
    job_numbers: jobNumbers,
    due_date: extra.dueDate ?? null,
    priority_rank: extra.priorityRank ?? null,
    completed: extra.completed ?? false,
  };
}

// Rows in the SERVER's canonical order (Hot rank → earliest due date →
// undated by received date → stocked-only rows last), the same order
// the view keeps.
const BOARD_ROWS = [
  {
    part_number: '2027-60-8114-00',
    hot_rank: 1,
    due_date: isoDateIn(2),
    received_date: isoDateIn(-10),
    locations: [
      location(AREA.cut, 'MACHINE', 4, 220, {
        machine: { id: 11, name: 'Saw 1' },
      }),
      location(AREA.lathe, 'MACHINE', 3, 125, {
        machine: { id: 23, name: 'Lathe 3' },
      }),
      location(AREA.lathe, 'QUEUE', 2, 70),
      location(AREA.lathe, 'DONE', 1, 25, {
        machine: { id: 23, name: 'Lathe 3' },
      }),
    ],
    active_quantity: 10,
    stocked_quantity: 0,
    scrapped_quantity: 1,
    total_quantity: 10,
    demands: [
      demand(7001, '007001', ['18112'], 10, {
        dueDate: isoDateIn(2),
        priorityRank: 1,
      }),
      demand(7008, '007008', ['18240'], 5, { dueDate: isoDateIn(2) }),
    ],
  },
  {
    part_number: '142-260',
    hot_rank: 2,
    due_date: isoDateIn(-6),
    received_date: isoDateIn(-18),
    locations: [
      location(AREA.external, 'PROCESSING', 20, 5880, { activity: 'plating' }),
    ],
    active_quantity: 20,
    stocked_quantity: 0,
    scrapped_quantity: 2,
    total_quantity: 20,
    demands: [
      demand(7005, '007005', ['18031'], 20, {
        dueDate: isoDateIn(-6),
        priorityRank: 2,
      }),
    ],
  },
  {
    part_number: '0123-40-0007-22',
    hot_rank: null,
    due_date: isoDateIn(9),
    received_date: isoDateIn(-3),
    locations: [
      location(AREA.external, 'PROCESSING', 12, 1800, { activity: 'vendor' }),
    ],
    active_quantity: 12,
    stocked_quantity: 0,
    scrapped_quantity: 0,
    total_quantity: 12,
    demands: [demand(7007, '007007', ['18377'], 12, { dueDate: isoDateIn(9) })],
  },
  {
    part_number: '0455-20-0118-03',
    hot_rank: null,
    due_date: isoDateIn(9),
    received_date: isoDateIn(-6),
    locations: [
      location(AREA.lathe, 'MACHINE', 4, 65, {
        machine: { id: 22, name: 'Lathe 2' },
      }),
      location(AREA.material, 'PROCESSING', 8, 2941),
    ],
    active_quantity: 12,
    stocked_quantity: 0,
    scrapped_quantity: 0,
    total_quantity: 12,
    demands: [demand(7003, '007003', ['18190'], 12, { dueDate: isoDateIn(9) })],
  },
  {
    part_number: '78-04-0031',
    hot_rank: null,
    due_date: isoDateIn(16),
    received_date: isoDateIn(-4),
    locations: [
      location(AREA.deburr, 'DONE', 3, 30),
      location(AREA.mill, 'MACHINE', 3, 45, {
        machine: { id: 31, name: 'Mill 1' },
      }),
    ],
    active_quantity: 6,
    stocked_quantity: 0,
    scrapped_quantity: 0,
    total_quantity: 6,
    demands: [
      demand(7002, '007002', ['18102'], 6, { dueDate: isoDateIn(16) }),
      // Internal MODIFY demand without an external WO Number → `—`.
      demand(7012, null, [], 1, { requestType: 'MODIFY' }),
    ],
  },
  {
    // WO Demand without a due date: valid data — sorts after all dated
    // demands, ordered by the parent Work Order received date.
    part_number: '118-052',
    hot_rank: null,
    due_date: null,
    received_date: isoDateIn(-2),
    locations: [location(AREA.manual, 'PROCESSING', 4, 320)],
    active_quantity: 4,
    stocked_quantity: 0,
    scrapped_quantity: 0,
    total_quantity: 4,
    demands: [demand(7011, '007011', ['18520'], 4)],
  },
  {
    part_number: '309-127',
    hot_rank: null,
    due_date: isoDateIn(-12),
    received_date: isoDateIn(-34),
    locations: [location(AREA.stockroom, 'STOCKED', 50, null)],
    active_quantity: 0,
    stocked_quantity: 50,
    scrapped_quantity: 0,
    total_quantity: 50,
    demands: [
      demand(6996, '006996', ['17740'], 50, {
        allocated: 50,
        dueDate: isoDateIn(-12),
      }),
    ],
  },
];

function boardPayload(rows = BOARD_ROWS) {
  return {
    department: { id: 1, name: 'Machine Shop' },
    rows,
    active_part_numbers: rows.filter((row) => row.active_quantity > 0).length,
    active_quantity: rows.reduce((sum, row) => sum + row.active_quantity, 0),
    stocked_quantity: rows.reduce((sum, row) => sum + row.stocked_quantity, 0),
    scrapped_quantity: rows.reduce(
      (sum, row) => sum + row.scrapped_quantity,
      0,
    ),
  };
}

type FetchImpl = (input: RequestInfo | URL) => Promise<Response>;

/** Health ok + the board answered from `boardResponse` (overridable). */
function stubFetch(
  boardResponse: () => Promise<Response> = () =>
    Promise.resolve(
      new Response(JSON.stringify(boardPayload()), { status: 200 }),
    ),
) {
  const impl = vi.fn<FetchImpl>((input) => {
    const url = String(input);
    if (url.startsWith('/api/production-board')) return boardResponse();
    return Promise.resolve(
      new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
    );
  });
  vi.stubGlobal('fetch', impl);
  return impl;
}

beforeEach(() => {
  vi.useFakeTimers();
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function boardTree() {
  return (
    <ThemeProvider>
      <ConnectivityProvider>
        <RouterProvider>
          <ProductionBoardView />
        </RouterProvider>
      </ConnectivityProvider>
    </ThemeProvider>
  );
}

async function renderBoard(path = '/production-board') {
  window.history.replaceState({}, '', path);
  const view = render(boardTree());
  await act(async () => {});
  return view;
}

function visibleTable() {
  const tables = document.querySelectorAll('table.pb-table');
  // The second .pb-table is the hidden measurement copy.
  return tables[0] as HTMLTableElement;
}

function rowByPn(pn: string) {
  return Array.from(visibleTable().querySelectorAll('tbody tr')).find((tr) =>
    tr.querySelector('.part')?.textContent?.includes(pn),
  );
}

test('columns render in the approved order with Job Numbers last', async () => {
  await renderBoard();

  // Headers carrying a tooltip wrap their label in .thlbl — the
  // column NAME is the label, never the tooltip text (v18).
  const headers = Array.from(
    visibleTable().querySelectorAll('thead th'),
    (th) => th.querySelector('.thlbl')?.textContent ?? th.textContent,
  );
  expect(headers).toEqual([
    'No.',
    'Part Number',
    'Areas & Quantities · Time',
    'Due Date',
    'Total Days',
    'Job Numbers',
  ]);
  // Content-driven sizing: semantic <colgroup> classes, no inline
  // percentage widths; remaining width is distributed through CSS on
  // the col classes, never through per-cell style attributes.
  const cols = Array.from(
    visibleTable().querySelectorAll('colgroup col'),
    (col) => col.className,
  );
  expect(cols).toEqual([
    'col-no',
    'col-pn',
    'col-areas',
    'col-due',
    'col-days',
    'col-jobs',
  ]);
  expect(visibleTable().querySelector('th[style]')).toBeNull();
});

test('the PN cell holds only the PN (15ch intrinsic minimum) and description', async () => {
  await renderBoard();

  // `.pb-table .part` owns the 15ch min-width + nowrap that keep a
  // standard PN such as `2027-60-8114-00` fully displayed; longer PNs
  // expand the column (jsdom cannot measure the pixel width, so the
  // class/structure is the regression surface). No Hot indicator is
  // ever rendered inside the PN cell.
  const th = visibleTable().querySelector('th.pn');
  expect(th?.textContent).toBe('Part Number');
  const cell = rowByPn('2027-60-8114-00')?.querySelector('td.pn');
  expect(cell).toBeDefined();
  const part = cell?.querySelector('.part');
  expect(part?.textContent).toBe('2027-60-8114-00');
  expect(cell?.textContent).not.toContain('🔥');
});

test('a longer PN renders fully in the PN cell without ellipsis markup', async () => {
  await renderBoard('/production-board?state=long');

  const part = rowByPn('0118-40-0022-07-0455-88-REV-C')?.querySelector('.part');
  expect(part?.textContent).toBe('0118-40-0022-07-0455-88-REV-C');
});

test('Hot rows carry the flame in the No. column only, with an accessible label', async () => {
  await renderBoard();

  const hotRow = rowByPn('2027-60-8114-00');
  const no = hotRow?.querySelector('.cell-no .no');
  expect(no?.textContent).toContain('1');
  expect(no?.textContent).toContain('🔥');
  expect(no?.getAttribute('aria-label')).toBe('Row 1, Hot priority');
  // Exactly one flame in the whole row — never a second one elsewhere.
  const flames = (hotRow?.textContent?.match(/🔥/g) ?? []).length;
  expect(flames).toBe(1);

  // Non-Hot rows carry no flame and no Hot aria label.
  const plain = rowByPn('118-052');
  expect(plain?.textContent).not.toContain('🔥');
  expect(
    plain?.querySelector('.cell-no .no')?.getAttribute('aria-label'),
  ).toBeNull();
});

test('the legend conventions live in the column-header tooltips, not the footer', async () => {
  await renderBoard();

  // Each convention sits in the header of the column it describes
  // (v18), as key/description rows inside a hover tooltip panel.
  const ths = Array.from(visibleTable().querySelectorAll('thead th'));
  const thByLabel = (label: string) =>
    ths.find((th) => th.querySelector('.thlbl')?.textContent === label);

  const noTip = thByLabel('No.')?.querySelector('.th-tip');
  expect(noTip).not.toBeNull();
  expect(noTip?.querySelector('.tipkey')?.textContent).toBe('🔥');
  expect(noTip?.textContent).toContain('Hot priority (highest first)');

  const dueTip = thByLabel('Due Date')?.querySelector('.th-tip');
  const dueRows = Array.from(dueTip?.querySelectorAll('.tiprow') ?? []);
  expect(dueRows).toHaveLength(5);
  expect(dueRows[0].querySelector('.tipkey')?.textContent).toBe(
    'Blinking days count',
  );
  // Kept short so the row fits the panel on one line — the
  // steady-date/PN parenthetical is gone.
  expect(dueRows[0].querySelector('.tipdesc')?.textContent).toBe(
    'due soon / overdue',
  );
  // The former `— no due date` row is gone; instead the tooltip
  // explains the Due Soon window — every number derived from the
  // shared policy (views/dates), never duplicated literals — with a
  // few lead-time examples: clamp(min 2, ceil(lead × 15%), max 7).
  expect(dueTip?.textContent).not.toContain('no due date');
  expect(dueRows[1].querySelector('.tipkey')?.textContent).toBe('Due soon');
  expect(dueRows[1].querySelector('.tipdesc')?.textContent).toContain(
    '15% of the lead time',
  );
  expect(dueRows[1].querySelector('.tipdesc')?.textContent).toContain(
    '2–7 days',
  );
  expect(dueRows[2].querySelector('.tipkey')?.textContent).toBe('10-day lead');
  expect(dueRows[2].querySelector('.tipdesc')?.textContent).toContain(
    'warns 2 days ahead',
  );
  expect(dueRows[3].querySelector('.tipkey')?.textContent).toBe('30-day lead');
  expect(dueRows[3].querySelector('.tipdesc')?.textContent).toContain(
    'warns 5 days ahead',
  );
  expect(dueRows[4].querySelector('.tipkey')?.textContent).toBe('90-day lead');
  expect(dueRows[4].querySelector('.tipdesc')?.textContent).toContain(
    'warns 7 days ahead',
  );

  // Job Numbers carries no tooltip anymore (v19) — the `— no external
  // WO Number` note was unnecessary detail for a read-only board. The
  // header is a plain <th> without the tooltip label wrapper.
  const jobsTh = ths.find((th) => th.textContent === 'Job Numbers');
  expect(jobsTh).toBeDefined();
  expect(jobsTh?.classList.contains('hastip')).toBe(false);
  expect(jobsTh?.querySelector('.th-tip')).toBeNull();

  // The other headers carry no tooltip.
  expect(
    visibleTable().querySelectorAll('thead th.hastip .th-tip'),
  ).toHaveLength(2);

  // The footer legend row no longer repeats the conventions — only
  // the sorting rule remains there.
  const foot = document.querySelector('.pb-foot');
  expect(foot?.textContent).not.toContain('Hot priority');
  expect(foot?.textContent).not.toContain('blinking days count');
  expect(foot?.textContent).not.toContain('no external WO Number');
  expect(foot?.textContent).not.toContain('⊘');
  expect(foot?.textContent).toContain('Sorted: Hot rank first');

  // Stylesheet: hover-only tooltip panel, hidden otherwise (adds no
  // height to the sticky header). The right-anchored variant left with
  // the Job Numbers tooltip (v19) — no rule may linger unused.
  const css = await readBoardCss();
  expect(ruleBlock(css, '.pb-table .th-tip')).toContain('display: none');
  expect(css).toMatch(/th\.hastip:hover \.th-tip \{[^}]*display: flex/);
  expect(css).not.toContain('.th-tip.right');
  // Long descriptions wrap INSIDE the panel instead of overflowing
  // its border: no nowrap on the rows or descriptions — only the key
  // itself never wraps.
  expect(ruleBlock(css, '.pb-table .th-tip .tiprow')).not.toContain('nowrap');
  expect(ruleBlock(css, '.pb-table .th-tip .tipkey')).toContain(
    'white-space: nowrap',
  );
});

test('the footer states the sorting rule in user language', async () => {
  await renderBoard();

  const foot = document.querySelector('.pb-foot');
  expect(foot?.textContent).toContain(
    'Sorted: Hot rank first → earliest due date → no due date by oldest received date.',
  );
  // No deterministic tie-breakers and no implementation field names.
  expect(foot?.textContent).not.toMatch(/tie-break|hotRank|received:/);
});

test('the footer is separated into readable control and legend rows', async () => {
  await renderBoard();

  const rows = document.querySelectorAll('.pb-foot .pb-footrow');
  expect(rows).toHaveLength(2);
  // Controls + aggregate totals in the first row…
  expect(rows[0].querySelector('.pgnav')).not.toBeNull();
  expect(rows[0].textContent).toContain('pcs in production');
  // …and only the sorting rule in the legend row (v18 — the flame /
  // blink / dash conventions live in the column-header tooltips).
  const legends = Array.from(rows[1].querySelectorAll('.leg'), (el) =>
    el.textContent?.trim(),
  );
  expect(legends).toHaveLength(1);
  expect(legends[0]).toContain('Sorted: Hot rank first');
});

test('the footer anchors through flex layout — never position: fixed', async () => {
  // jsdom applies no layout, so the anchoring contract is checked at
  // the stylesheet level: the board is a flex column, the footer is a
  // normal flex child pushed down with margin-top: auto, and nothing
  // uses position: fixed (a fixed footer would cover table content and
  // fall out of the pagination measurement).
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  expect(css).toMatch(/\.pb \{[^}]*flex-direction: column/);
  expect(css).toMatch(/\.pb-foot \{[^}]*margin-top: auto/);
  expect(css).not.toContain('position: fixed');
  // No view-root viewport calculation survives (shell flex layout).
  expect(css).not.toContain('100vh');
});

test('only the flame pulses: subtle animation with a reduced-motion fallback', async () => {
  await renderBoard();

  // The flame markup lives in the No. column…
  const hotNo = document.querySelector('tr.hotrow1 .no');
  expect(hotNo?.querySelector('.hotflame')).not.toBeNull();

  // …and the animation contract lives in the stylesheet: a dedicated
  // flame keyframe applied to the flame only (never the row, the row
  // number element, or the PN), disabled under prefers-reduced-motion
  // while the flame and Hot styling stay.
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  expect(css).toContain('@keyframes pb-flame-pulse');
  expect(css).toMatch(/\.no \.hotflame \{[^}]*animation: pb-flame-pulse/);
  expect(css).toMatch(
    /prefers-reduced-motion[^{]*\{\s*\.pb-table \.no \.hotflame \{[^}]*animation: none/,
  );
  expect(css).not.toMatch(/tr\.hotrow1[^{]*\{[^}]*animation/);
  expect(css).not.toMatch(/\.part \{[^}]*animation/);
});

test('location tracks share cross-row minimums in the stylesheet', async () => {
  // Cross-row alignment cannot be measured in jsdom; the CSS contract
  // is: shared measured track widths (the --loc-* custom properties
  // with small ch fallbacks + max-content growth) for
  // Location | Quantity | State | Time, with a trailing 1fr spacer so
  // time stays near its location data instead of the far cell edge.
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  const loc = css.match(/\.pb-table \.loc \{[^}]*}/)?.[0] ?? '';
  expect(loc).toContain('grid-template-columns');
  expect(
    loc.match(/minmax\(var\(--loc-\w+, \d+ch\), max-content\)/g)?.length,
  ).toBe(4);
  expect(loc).toMatch(/1fr;/);
  // Explicit track assignment keeps every row inside the shared tracks.
  expect(css).toMatch(/\.locrow \.lname \{[^}]*grid-column: 1/);
  expect(css).toMatch(/\.locrow \.ltime \{[^}]*grid-column: 4/);
});

test('the clock reads time-first with the date as its secondary line', async () => {
  await renderBoard();

  const wrap = document.querySelector('.pb-head .clockwrap');
  const children = Array.from(wrap?.children ?? [], (el) => el.className);
  expect(children).toEqual(['clockrow', 'clockdate']);
  // The time row leads; standard mode renders no control inside it
  // (the kiosk theme control lives here in kiosk mode, v17).
  const row = wrap?.querySelector('.clockrow');
  expect(Array.from(row?.children ?? [], (el) => el.className)).toEqual([
    'clock',
  ]);
});

test('rows follow canonical order: Hot → dated → undated → stocked', async () => {
  await renderBoard();

  const parts = Array.from(
    visibleTable().querySelectorAll('tbody .part'),
    (el) => el.textContent,
  );
  // 0123-40-0007-22 and 0455-20-0118-03 share the SAME due date in the
  // v16 mocks — equal dated demands keep the stable creation order
  // (demand-order rule 4), so 0123 (earlier in the mock array) leads.
  expect(parts).toEqual([
    '2027-60-8114-00',
    '142-260',
    '0123-40-0007-22',
    '0455-20-0118-03',
    '78-04-0031',
    '118-052',
    '309-127',
  ]);
  // Row numbering stays continuous in display order.
  const numbers = Array.from(
    visibleTable().querySelectorAll('tbody .cell-no .no'),
    (el) => el.textContent?.replace('🔥', '').trim(),
  );
  expect(numbers).toEqual(['1', '2', '3', '4', '5', '6', '7']);
});

test('a missing due date renders as — / No due date, not as an error', async () => {
  await renderBoard();

  const undatedRow = rowByPn('118-052');
  expect(undatedRow).toBeDefined();
  expect(undatedRow?.querySelector('.due .d1')?.textContent).toBe('—');
  expect(undatedRow?.querySelector('.due .d2')?.textContent).toBe(
    'No due date',
  );
});

test('only the urgency text blinks — never the PN, never the date', async () => {
  await renderBoard();

  const table = visibleTable();
  // Urgent rows blink their days-count line…
  const blinking = Array.from(table.querySelectorAll('tbody .due .d2.blink'));
  expect(blinking.length).toBeGreaterThan(0);
  blinking.forEach((el) => {
    expect(['2 days left', 'overdue 6 days']).toContain(el.textContent);
  });
  // …while no PN and no date value carries the blink class.
  expect(table.querySelectorAll('.part.blink')).toHaveLength(0);
  expect(table.querySelectorAll('.due .d1.blink')).toHaveLength(0);
  // Non-urgent rows do not blink.
  expect(table.querySelectorAll('.d2.ok.blink')).toHaveLength(0);
});

test('Areas & Quantities rows share aligned label/qty/state/time columns', async () => {
  await renderBoard();

  const firstLoc = visibleTable().querySelector('tbody .loc');
  expect(firstLoc).not.toBeNull();
  // Every row — including the total line — uses the same four aligned
  // columns: Location | Quantity | State/activity | Time.
  const rows = Array.from(firstLoc!.querySelectorAll('.locrow'));
  expect(rows.length).toBeGreaterThan(1);
  rows.forEach((row) => {
    expect(row.querySelector('.lname')).not.toBeNull();
    expect(row.querySelector('.lqty')).not.toBeNull();
    expect(row.querySelector('.ltag')).not.toBeNull();
    expect(row.querySelector('.ltime')).not.toBeNull();
  });
  // The `total … pcs` line starts at the Location column and its
  // quantity uses the same quantity column as the rows above.
  const total = firstLoc!.querySelector('.locrow.total');
  expect(total?.querySelector('.lname')?.textContent).toBe('total');
  expect(total?.querySelector('.lqty')?.textContent).toBe('10');
  expect(total?.querySelector('.ltag')?.textContent).toBe('pcs');
});

test('one continuous separator element sits between locations and the total row', async () => {
  await renderBoard();

  const locs = Array.from(visibleTable().querySelectorAll('tbody .loc'));
  expect(locs.length).toBeGreaterThan(0);
  for (const loc of locs) {
    // Exactly one dedicated separator spanning the grid — never
    // per-cell border fragments on the total row's children.
    expect(loc.querySelectorAll('.locsep')).toHaveLength(1);
    const children = Array.from(loc.children).map((el) => el.className);
    const sepIndex = children.findIndex((c) => c.includes('locsep'));
    const totalIndex = children.findIndex((c) => c.includes('total'));
    expect(sepIndex).toBeGreaterThan(-1);
    expect(totalIndex).toBe(sepIndex + 1);
  }
});

test('active Machine rows render dot + machine chip + qty + `on machine` + time', async () => {
  await renderBoard();

  const loc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  const machineRow = Array.from(loc?.querySelectorAll('.locrow') ?? []).find(
    (row) => row.querySelector('.mchip')?.textContent === 'Lathe 3',
  );
  expect(machineRow).toBeDefined();
  // Area identity dot inside the label cell.
  expect(machineRow?.querySelector('.lname .areadot')).not.toBeNull();
  // The Machine name lives only in the chip element.
  expect(machineRow?.querySelector('.lname .mchip')?.textContent).toBe(
    'Lathe 3',
  );
  expect(machineRow?.querySelector('.lqty')?.textContent).toBe('3');
  expect(machineRow?.querySelector('.ltag')?.textContent).toBe('on machine');
  expect(machineRow?.querySelector('.ltime')?.textContent).toBe('2h 05m');
});

test('the machine chip carries the explicit machine field — no combined Area label', async () => {
  await renderBoard();

  const loc = rowByPn('0455-20-0118-03')?.querySelector('.loc');
  const chips = Array.from(loc?.querySelectorAll('.mchip') ?? []);
  expect(chips.map((chip) => chip.textContent)).toEqual(['Lathe 2']);
  // The label cell shows only the chip — never an old-style combined
  // `Lathe 2` Area label rendered as plain text.
  const chipLabel = chips[0].closest('.lname');
  expect(chipLabel?.textContent).toBe('Lathe 2');
  const plainLatheTwoLabels = Array.from(
    visibleTable().querySelectorAll('.lname'),
  ).filter(
    (lname) =>
      lname.textContent?.includes('Lathe 2') && !lname.querySelector('.mchip'),
  );
  expect(plainLatheTwoLabels).toHaveLength(0);
});

test('done rows show the Area label + done state and never imply Machine ownership', async () => {
  await renderBoard();

  // Done with completion context (Lathe): Area label, success-tone
  // `done`, the completing Machine only as a tooltip — no chip, no
  // `on machine`.
  const latheLoc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  const doneRow = Array.from(latheLoc?.querySelectorAll('.locrow') ?? []).find(
    (row) => row.querySelector('.ltag.done'),
  );
  expect(doneRow).toBeDefined();
  expect(doneRow?.querySelector('.lname')?.textContent).toBe('Lathe');
  expect(doneRow?.querySelector('.mchip')).toBeNull();
  expect(doneRow?.querySelector('.lqty')?.textContent).toBe('1');
  const doneTag = doneRow?.querySelector('.ltag.done');
  expect(doneTag?.textContent).toBe('done');
  expect(doneTag?.getAttribute('title')).toBe(
    'Completed at Lathe 3 — ready to transfer',
  );
  expect(doneRow?.textContent).not.toContain('on machine');

  // Done in a no-Machine Area (Deburr): no completion context.
  const deburrLoc = rowByPn('78-04-0031')?.querySelector('.loc');
  const deburrDone = Array.from(
    deburrLoc?.querySelectorAll('.locrow') ?? [],
  ).find((row) => row.querySelector('.ltag.done'));
  expect(deburrDone?.querySelector('.lname')?.textContent).toBe('Deburr');
  expect(deburrDone?.querySelector('.lqty')?.textContent).toBe('3');
  expect(deburrDone?.querySelector('.ltag.done')?.getAttribute('title')).toBe(
    'Completed — ready to transfer',
  );
  expect(deburrDone?.querySelector('.mchip')).toBeNull();
});

test('queue and processing rows keep their state labels', async () => {
  await renderBoard();

  const latheLoc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  const queueRow = Array.from(latheLoc?.querySelectorAll('.locrow') ?? []).find(
    (row) => row.querySelector('.ltag')?.textContent === 'queue',
  );
  expect(queueRow?.querySelector('.lname')?.textContent).toBe('Lathe');
  expect(queueRow?.querySelector('.lqty')?.textContent).toBe('2');

  const manualLoc = rowByPn('118-052')?.querySelector('.loc');
  const processingRow = Array.from(
    manualLoc?.querySelectorAll('.locrow') ?? [],
  ).find((row) => row.querySelector('.ltag')?.textContent === 'processing');
  expect(processingRow?.querySelector('.lname')?.textContent).toBe('Manual');
});

test('state tones color the quantity values — state words stay quiet secondary text', async () => {
  await renderBoard();

  // Markup: every location row carries its explicit `st-*` state
  // class (v18), so the stylesheet can tone the row's QUANTITY by
  // state; the total row stays outside the state classes.
  const latheLoc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  const rows = Array.from(latheLoc?.querySelectorAll('.locrow') ?? []);
  const byTag = (text: string) =>
    rows.find((row) => row.querySelector('.ltag')?.textContent === text);
  expect(byTag('on machine')?.classList.contains('st-machine')).toBe(true);
  expect(byTag('queue')?.classList.contains('st-queue')).toBe(true);
  expect(byTag('done')?.classList.contains('st-done')).toBe(true);
  const total = latheLoc?.querySelector('.locrow.total');
  expect(total?.className).toBe('locrow total');

  // Stylesheet: the semantic tone lives on the quantity value —
  // queue → warning, on machine / processing → information, done →
  // success — never on the state words, which read as the row's
  // quietest (dim) text. Color is never the only distinction: the
  // written state stays beside every toned quantity.
  const css = await readBoardCss();
  expect(ruleBlock(css, '.pb-table .locrow.st-queue .lqty')).toContain(
    'color: var(--warn-t)',
  );
  expect(ruleBlock(css, '.pb-table .locrow.st-processing .lqty')).toContain(
    'color: var(--info-t)',
  );
  expect(ruleBlock(css, '.pb-table .locrow.st-done .lqty')).toContain(
    'color: var(--ok-t)',
  );
  // v19 tone swap: the state words take the dim tone the time used to
  // carry, the dwell time reads as secondary text.
  expect(ruleBlock(css, '.pb-table .ltag')).toContain('color: var(--faint)');
  expect(ruleBlock(css, '.pb-table .ltime')).toContain('color: var(--muted)');
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
  expect(stripped).not.toMatch(/\.ltag\.st-[\w-]+[^{]*\{[^}]*color/);
  expect(stripped).not.toMatch(/\.ltag\.done[^{,]*\{/);
});

test('scrap renders as plain error-toned text on the total line, right-anchored, no ⊘', async () => {
  await renderBoard();

  const loc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  // The text lives inside the total row's right-hand time cell — the
  // same line as `total … pcs`, never an extra row.
  const chip = loc?.querySelector('.locrow.total .ltime .scraptext');
  expect(chip?.textContent).toBe('1 scrapped');
  expect(loc?.querySelector('.locrow.scrap')).toBeNull();
  expect(loc?.textContent).not.toContain('⊘');

  // Multi-piece wording comes straight from the value.
  const platedLoc = rowByPn('142-260')?.querySelector('.loc');
  expect(
    platedLoc?.querySelector('.locrow.total .ltime .scraptext')?.textContent,
  ).toBe('2 scrapped');

  // Zero scrap renders no chip at all.
  const cleanLoc = rowByPn('118-052')?.querySelector('.loc');
  expect(cleanLoc?.querySelector('.scraptext')).toBeNull();
});

test('External locations show only `External` plus an activity chip', async () => {
  await renderBoard();

  // External — Plating became: label `External`, activity chip
  // `plating` in the state position (replacing `processing`).
  const platingLoc = rowByPn('142-260')?.querySelector('.loc');
  const platingRow = platingLoc?.querySelector('.locrow:not(.total)');
  expect(platingRow?.querySelector('.lname')?.textContent).toBe('External');
  expect(platingRow?.querySelector('.lname')?.textContent).not.toContain('—');
  const platingChip = platingRow?.querySelector('.ltag .actchip');
  expect(platingChip?.textContent).toBe('plating');
  expect(platingRow?.textContent).not.toContain('processing');

  const vendorLoc = rowByPn('0123-40-0007-22')?.querySelector('.loc');
  const vendorRow = vendorLoc?.querySelector('.locrow:not(.total)');
  expect(vendorRow?.querySelector('.lname')?.textContent).toBe('External');
  expect(vendorRow?.querySelector('.ltag .actchip')?.textContent).toBe(
    'vendor',
  );

  // Normal non-External Areas keep their plain state labels.
  const manualLoc = rowByPn('118-052')?.querySelector('.loc');
  expect(
    manualLoc?.querySelector('.locrow:not(.total) .ltag')?.textContent,
  ).toBe('processing');
  expect(manualLoc?.querySelector('.actchip')).toBeNull();
});

test('a long Machine name renders in full — the chip never truncates', async () => {
  await renderBoard('/production-board?state=long');

  const chip = Array.from(visibleTable().querySelectorAll('.mchip')).find(
    (el) => el.textContent === 'Mill 3 — Horizontal Boring',
  );
  expect(chip).toBeDefined();
  expect(chip?.closest('.locrow')?.querySelector('.ltag')?.textContent).toBe(
    'on machine',
  );
  // The chip styling carries no ellipsis truncation — a long Machine
  // name expands the Areas column minimum width instead (CSS-level
  // regression checked in the stylesheet test below).
});

test('the board stylesheet owns its heartbeat and never clips Machine names or PNs', async () => {
  // jsdom applies no layout, so the CSS contract is checked at the
  // stylesheet level: a Production Board-owned heartbeat animation
  // (with a reduced-motion fallback), no Scan Station animation
  // dependency, and no ellipsis truncation for the Machine chip, the
  // location label, or the PN.
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  // The Live dot uses the ONE shared connected heartbeat defined in
  // styles/global.css — no board-local duplicate keyframes.
  expect(css).toContain('animation: pf-heartbeat');
  expect(css).not.toContain('@keyframes pb-live-pulse');
  expect(css).not.toContain('ss-pulse');
  expect(css).toContain('prefers-reduced-motion');
  expect(css).toMatch(/\.live\.stale \.ld \{[^}]*animation: none/);
  // The `● Live` status carries the CURRENT connection tone (post-v18):
  // success while healthy, warning while stale — semantic tokens only.
  expect(css).toMatch(/\.live \.livestatus \{[^}]*var\(--ok-t\)/);
  expect(css).toMatch(/\.live\.stale \.livestatus \{[^}]*var\(--warn-t\)/);
  expect(css).not.toMatch(/\.mchip \{[^}]*text-overflow/);
  expect(css).not.toMatch(/\.lname \{[^}]*text-overflow/);
  // Intrinsic 15ch PN minimum instead of an arbitrary pixel width.
  expect(css).toMatch(/\.part \{[^}]*min-width: 15ch/);
  expect(css).not.toContain('min-width: 310px');
  // Shared content-driven location tracks: the visible rows consume
  // the measured `--loc-*` widths (small ch fallbacks only), while the
  // measurement copy keeps pure content sizing so the measurement has
  // no feedback loop.
  for (const field of ['lname', 'lqty', 'ltag', 'ltime']) {
    expect(css).toContain(`var(--loc-${field}`);
  }
  expect(css).toMatch(/\.pb-measure \.pb-table \.loc \{/);
});

test('the identity group shows the Department line and the live board title', async () => {
  await renderBoard();
  // Scan Station-style identity (v17, restructured post-v18): the
  // Department line above the `Production` title; the `● Live` status
  // sits directly after the title in the connection tone and carries
  // the status dot (Area-indicator geometry, shared heartbeat via
  // CSS) — the dot lives inside the status, not before the title.
  const headid = document.querySelector('.pb-head .pb-headid');
  expect(headid?.querySelector('.dept')?.textContent).toBe('Machine Shop');
  const live = headid?.querySelector('h1.live');
  expect(live?.childNodes[0]?.textContent).toBe('Production');
  const status = live?.querySelector('.livestatus');
  expect(status?.textContent).toBe('Live');
  expect(status?.querySelector('.ld')).not.toBeNull();
  expect(live?.textContent).not.toContain('Live Production');
  expect(live?.className).not.toContain('stale');
  // Healthy state renders no stale note.
  expect(live?.querySelector('.stalenote')).toBeNull();
});

test('the hidden measurement table renders the identical row structure', async () => {
  await renderBoard();

  const tables = document.querySelectorAll('table.pb-table');
  expect(tables).toHaveLength(2);
  // Same BoardRowCells component, same props on page 1 → identical
  // markup; pagination heights are therefore measured on the real row
  // structure (machine chips, done rows, scrap lines included).
  const visibleRow = tables[0].querySelector('tbody tr');
  const measureRow = tables[1].querySelector('tbody tr');
  expect(measureRow?.innerHTML).toBe(visibleRow?.innerHTML);
});

test('the header shows the live local date and time', async () => {
  await renderBoard();

  const clock = document.querySelector('.pb-head .clock');
  const date = document.querySelector('.pb-head .clockdate');
  expect(clock?.textContent).toMatch(/\d{1,2}:\d{2}:\d{2}/);
  expect(date?.textContent).toMatch(/\d{4}/);

  const before = clock?.textContent;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(document.querySelector('.pb-head .clock')?.textContent).not.toBe(
    before,
  );
});

test('long data paginates and rotates automatically; single pages never claim rotation', async () => {
  await renderBoard('/production-board?state=long');

  // 25 long rows → 3 pages (fallback page size in DOM environments
  // without layout; real heights drive this in the browser).
  expect(screen.getByText('Page 1 / 3')).toBeInTheDocument();

  // Per-page rotation (v15): page 1 shows 10 rows → 10 × 3 s = 30 s.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // Page 2 dwells another 30 s (10 rows); the 5-row last page only
  // 15 s — then rotation wraps around after the last page. Advanced in
  // two steps: each page's timer is armed in an effect AFTER the page
  // change commits, so one combined 45 s advance would end exactly at
  // the deadline of a timer that did not exist when the advance began.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
});

test('rotation fires exactly at the current page’s computed duration — never before', async () => {
  await renderBoard('/production-board?state=long');

  // 10 rows on page 1 → rotationDurationMs(10) = 30 s. One
  // millisecond before the deadline nothing rotates…
  await act(async () => {
    await vi.advanceTimersByTimeAsync(29_999);
  });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
  // …and the deadline itself advances the page.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // The 5-row last page dwells only 5 × 3 s = 15 s (still above the
  // 6 s floor) — shorter than a full page, and again never earlier.
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(14_999);
  });
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
});

test('manual Previous/Next and page dots navigate without wrapping', async () => {
  await renderBoard('/production-board?state=long');

  const prev = screen.getByRole('button', { name: 'Previous page' });
  const next = screen.getByRole('button', { name: 'Next page' });
  // First page: Previous disabled, Next enabled.
  expect(prev).toBeDisabled();
  expect(next).toBeEnabled();

  fireEvent.click(next);
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled();

  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
  // Last page: Next disables — manual navigation never wraps.
  expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();

  // Clickable page dots jump directly; the active dot is marked.
  const dot1 = screen.getByRole('button', { name: 'Go to page 1' });
  fireEvent.click(dot1);
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
  expect(
    screen
      .getByRole('button', { name: 'Go to page 1' })
      .getAttribute('aria-current'),
  ).toBe('page');
  expect(
    screen
      .getByRole('button', { name: 'Go to page 2' })
      .getAttribute('aria-current'),
  ).toBeNull();
});

test('page changes replay a direction-aware entry transition on the visible table', async () => {
  await renderBoard('/production-board?state=long');

  const visibleTable = () =>
    Array.from(document.querySelectorAll('table.pb-table')).find(
      (el) => !el.closest('.pb-measure'),
    )!;
  // Initial mount: no transition class — nothing slides in on load.
  expect(visibleTable().className).toBe('pb-table');

  // Forward navigation slides in from the right…
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(visibleTable().className).toContain('pb-pagefwd');
  // …and backward from the left.
  fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
  expect(visibleTable().className).toContain('pb-pageback');

  // A direct dot jump to a higher page is forward too…
  fireEvent.click(screen.getByRole('button', { name: 'Go to page 3' }));
  expect(visibleTable().className).toContain('pb-pagefwd');
  // …and the auto-rotation wrap (last page → first) stays forward:
  // the rotation keeps cycling in one direction.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
  expect(visibleTable().className).toContain('pb-pagefwd');

  // The stylesheet owns the one-shot entry animations, disabled under
  // prefers-reduced-motion (pages then change instantly).
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  expect(css).toContain('@keyframes pb-pagefwd');
  expect(css).toContain('@keyframes pb-pageback');
  expect(css).toMatch(
    /prefers-reduced-motion[^{]*\{\s*\.pb-table\.pb-pagefwd,\s*\.pb-table\.pb-pageback \{[^}]*animation: none/,
  );
});

test('manual navigation restarts the auto-rotation timer', async () => {
  await renderBoard('/production-board?state=long');

  // 25 s into the 30 s page-1 cycle a manual navigation happens…
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25_000);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // …so 25 s later (50 s from the old timer's start — well past its
  // 30 s deadline) nothing rotates yet: the timer restarted at the
  // manual change.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // The full 30 s page-2 interval after the manual change, rotation
  // resumes.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
});

test('ArrowLeft/ArrowRight navigate pages regardless of focus, without wrapping', async () => {
  await renderBoard('/production-board?state=long');

  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
  // No wrap at the last page.
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();

  fireEvent.keyDown(window, { key: 'ArrowLeft' });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  fireEvent.keyDown(window, { key: 'ArrowLeft' });
  fireEvent.keyDown(window, { key: 'ArrowLeft' });
  // No wrap at the first page.
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
});

test('modifier chords and active modal dialogs never navigate pages', async () => {
  await renderBoard('/production-board?state=long');

  fireEvent.keyDown(window, { key: 'ArrowRight', ctrlKey: true });
  fireEvent.keyDown(window, { key: 'ArrowRight', altKey: true });
  fireEvent.keyDown(window, { key: 'ArrowRight', metaKey: true });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();

  // While an application modal dialog is open, the board shortcut is
  // inert.
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  document.body.appendChild(dialog);
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
  dialog.remove();

  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
});

test('keyboard navigation restarts the rotation timer and cleans up on unmount', async () => {
  const view = await renderBoard('/production-board?state=long');

  await act(async () => {
    await vi.advanceTimersByTimeAsync(25_000);
  });
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25_000);
  });
  // 50 s from mount — past the old 30 s deadline, still inside the
  // restarted one: the timer restarted at the arrow navigation.
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // After unmount the window listener is removed — no state updates,
  // no act() warnings, no errors.
  view.unmount();
  fireEvent.keyDown(window, { key: 'ArrowRight' });
});

/* ============ Touch swipe page navigation (post-v18) ============ */

function swipe(
  el: Element,
  from: [number, number],
  to: [number, number],
): void {
  fireEvent.touchStart(el, {
    touches: [{ clientX: from[0], clientY: from[1] }],
  });
  fireEvent.touchEnd(el, {
    changedTouches: [{ clientX: to[0], clientY: to[1] }],
  });
}

test('a horizontal swipe navigates pages without wrapping', async () => {
  await renderBoard('/production-board?state=long');
  const board = document.querySelector('.pb')!;

  // Swipe left (finger travels left) → next page, like Next/ArrowRight.
  swipe(board, [300, 200], [180, 210]);
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  swipe(board, [300, 200], [180, 190]);
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
  // No wrap on the last page.
  swipe(board, [300, 200], [180, 200]);
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();

  // Swipe right → previous page; no wrap on the first page.
  swipe(board, [180, 200], [300, 200]);
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  swipe(board, [180, 200], [300, 200]);
  swipe(board, [180, 200], [300, 200]);
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
});

test('short, vertical, and multi-touch gestures never change pages', async () => {
  await renderBoard('/production-board?state=long');
  const board = document.querySelector('.pb')!;

  // Below the minimum travel: a tap or slight drag.
  swipe(board, [200, 200], [170, 200]);
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
  // Predominantly vertical (scrolling), even with horizontal drift.
  swipe(board, [200, 100], [120, 400]);
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
  // A multi-touch gesture (pinch zoom) is ignored entirely.
  fireEvent.touchStart(board, {
    touches: [
      { clientX: 100, clientY: 100 },
      { clientX: 300, clientY: 100 },
    ],
  });
  fireEvent.touchEnd(board, {
    changedTouches: [{ clientX: 260, clientY: 100 }],
  });
  expect(screen.getByText(/Page 1 \/ 3/)).toBeInTheDocument();
});

test('a swipe restarts the auto-rotation timer like the other manual controls', async () => {
  await renderBoard('/production-board?state=long');
  const board = document.querySelector('.pb')!;

  // 25 s into the 30 s page-1 cycle, a swipe navigates…
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25_000);
  });
  swipe(board, [300, 200], [180, 200]);
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  // …so 25 s later — past the original deadline, inside the restarted
  // one — nothing rotates yet.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  // The full page-2 interval after the swipe, rotation resumes.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(screen.getByText(/Page 3 \/ 3/)).toBeInTheDocument();
});

test('the active page clamps when the page structure changes', async () => {
  const view = await renderBoard('/production-board?state=long');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // The data set shrinks to a single page: the page indicator resets
  // and no rotation (or rotation indicator) is claimed.
  window.history.replaceState({}, '', '/production-board');
  view.rerender(boardTree());
  await act(async () => {});
  expect(screen.getByText('Page 1 / 1')).toBeInTheDocument();
  expect(document.querySelector('.pb-rotate')).toBeNull();
});

/* ============ Rotation indicator (GUI v13) ============ */

test('a multi-page board shows the rotation indicator with track and seconds', async () => {
  await renderBoard('/production-board?state=long');

  const rotate = document.querySelector('.pb-rotate');
  expect(rotate).not.toBeNull();
  expect(rotate?.querySelector('.pb-rotatetrack i')).not.toBeNull();
  // Full countdown for the current page: 10 rows × 3 s (v15).
  expect(rotate?.querySelector('.pb-rotatesec')?.textContent).toBe('30 s');
  // The tooltip states the per-row rule instead of a fixed interval.
  expect(rotate?.getAttribute('title')).toBe(
    'Time until the next automatic page rotation (3 s per displayed row)',
  );
});

test('the indicator counts down against the same deadline that rotates the page', async () => {
  await renderBoard('/production-board?state=long');
  const sec = () => document.querySelector('.pb-rotatesec')?.textContent;

  await act(async () => {
    await vi.advanceTimersByTimeAsync(4_000);
  });
  expect(sec()).toBe('26 s');

  // When the same deadline elapses, the page rotates and the indicator
  // re-arms with the next page's own duration — one timing source,
  // never two unsynchronized timers.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(26_000);
  });
  expect(screen.getByText('Page 2 / 3')).toBeInTheDocument();
  expect(sec()).toBe('30 s');
});

test('manual navigation resets the rotation timer AND the indicator together', async () => {
  await renderBoard('/production-board?state=long');
  const sec = () => document.querySelector('.pb-rotatesec')?.textContent;

  await act(async () => {
    await vi.advanceTimersByTimeAsync(4_000);
  });
  expect(sec()).toBe('26 s');
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await act(async () => {});
  expect(screen.getByText('Page 2 / 3')).toBeInTheDocument();
  expect(sec()).toBe('30 s');

  // Arrow-key navigation resets the shared deadline the same way —
  // and the 5-row last page arms its own shorter 15 s duration.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(sec()).toBe('25 s');
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  await act(async () => {});
  expect(screen.getByText('Page 3 / 3')).toBeInTheDocument();
  expect(sec()).toBe('15 s');

  // The last page's full interval later the automatic rotation
  // continues (wrapping).
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(screen.getByText('Page 1 / 3')).toBeInTheDocument();
});

test('the indicator stylesheet supports reduced motion without losing the label', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  // Under prefers-reduced-motion the moving track hides; the seconds
  // label (outside that rule) remains.
  expect(css).toMatch(
    /prefers-reduced-motion[^{]*\{\s*\.pb-foot \.pb-rotatetrack \{\s*display: none/,
  );
});

/* ============ Footer aggregate summary (GUI v13) ============ */

test('the aggregate summary emphasizes values with tones and keeps labels quiet — no pills', async () => {
  await renderBoard();

  const agg = document.querySelector('.pb-foot .pb-agg');
  expect(agg).not.toBeNull();
  const items = Array.from(agg!.querySelectorAll('.aggitem'), (el) =>
    el.textContent?.trim(),
  );
  expect(items).toEqual([
    '6 active PNs',
    '64 pcs in production',
    '50 pcs stocked',
    '3 pcs scrapped',
  ]);
  // Values are emphasized elements with semantic tone classes; labels
  // stay plain text (no pill/chip markup anywhere in the summary).
  const nums = agg!.querySelectorAll('.aggnum');
  expect(nums).toHaveLength(4);
  expect(nums[0].className).toBe('aggnum');
  expect(nums[1].classList.contains('m')).toBe(true);
  expect(nums[2].classList.contains('d')).toBe(true);
  expect(nums[3].classList.contains('e')).toBe(true);
});

/* ============ Kiosk mode (GUI v13) ============ */

test('the kiosk route renders the coherent board-owned kiosk header', async () => {
  await renderBoard('/production-board/kiosk');

  expect(document.querySelector('.pb')?.className).toContain('kiosk');
  // ONE shared header (v15): the standard .pb-head with the kiosk
  // class added — never a separate kiosk-only actions column stack.
  const head = document.querySelector('.pb-head.pbk-head');
  expect(head).not.toBeNull();
  expect(head?.querySelector('.pbk-actions')).toBeNull();
  // Identity group (v17, restructured post-v18): the SAME Scan
  // Station-style identity as the standard header — Department line +
  // `Production` title with the `● Live` status after it; the kiosk
  // header renders NO app brand.
  const headid = head?.querySelector('.pb-headid');
  expect(headid?.querySelector('.pbk-brand')).toBeNull();
  expect(headid?.querySelector('.dept')?.textContent).toBe('Machine Shop');
  expect(headid?.querySelector('h1')?.childNodes[0]?.textContent).toBe(
    'Production',
  );
  expect(headid?.querySelector('h1 .livestatus')?.textContent).toBe('Live');
  // The SAME live status as the standard header (shared meaning,
  // shared heartbeat) — never a second `ONLINE` chip repeating the
  // same connectivity in the board header.
  expect(headid?.querySelector('.live .livestatus .ld')).not.toBeNull();
  expect(head?.querySelector('.connchip')).toBeNull();
  // Clock zone: the shared compact (borderless) theme control sits
  // INSIDE the clock's time row, centered on the time text (v17).
  expect(head?.querySelector('.clockrow .themetoggle.compact')).not.toBeNull();
  expect(head?.querySelector('.clockrow .clock')).not.toBeNull();
  // The explicit exit action exists but lives in the footer controls
  // row — its shortcut moved into the tooltip; no legend line
  // repeats it.
  expect(
    screen.getByRole('switch', { name: 'Kiosk mode' }),
  ).toBeInTheDocument();
  expect(head?.querySelector('.pb-kioskswitch')).toBeNull();
  expect(document.querySelector('.pb-foot')?.textContent).not.toContain(
    'Ctrl+Shift+K: exit kiosk mode',
  );
});

test('the kiosk switch sits in the footer controls row after the aggregate summary', async () => {
  const view = await renderBoard('/production-board/kiosk');

  // Inside the FIRST .pb-footrow (controls), directly after .pb-agg —
  // a normal layout child measured by pagination, never in the header
  // and never in the legend row. An On/Off slide switch (v18):
  // aria-checked mirrors the active route, the state is written text
  // beside the knob, and the shortcut lives in the tooltip instead of
  // a legend line.
  const controls = document.querySelector('.pb-foot .pb-footrow');
  const kioskSw = controls?.querySelector('.pb-kioskswitch');
  expect(kioskSw).not.toBeNull();
  expect(kioskSw?.getAttribute('role')).toBe('switch');
  expect(kioskSw?.getAttribute('aria-checked')).toBe('true');
  expect(kioskSw?.getAttribute('aria-label')).toBe('Kiosk mode');
  expect(kioskSw?.classList.contains('on')).toBe(true);
  expect(kioskSw?.querySelector('.track .knob')).not.toBeNull();
  expect(kioskSw?.querySelector('.swlbl')?.textContent).toBe('Kiosk');
  expect(kioskSw?.querySelector('.swstate')?.textContent).toBe('On');
  expect(kioskSw?.getAttribute('title')).toBe('Exit kiosk mode (Ctrl+Shift+K)');
  expect(kioskSw?.previousElementSibling?.className).toBe('pb-agg');
  expect(document.querySelector('.pb-head .pb-kioskswitch')).toBeNull();
  expect(
    document.querySelector('.pb-footrow.legend .pb-kioskswitch'),
  ).toBeNull();
  view.unmount();

  // Standard mode renders the SAME switch as the explicit kiosk entry
  // (v17) — same footer position, same presentation, state Off.
  await renderBoard('/production-board');
  const enter = document.querySelector('.pb-foot .pb-footrow .pb-kioskswitch');
  expect(enter?.getAttribute('aria-checked')).toBe('false');
  expect(enter?.classList.contains('on')).toBe(false);
  expect(enter?.querySelector('.swstate')?.textContent).toBe('Off');
  expect(enter?.getAttribute('title')).toBe('Enter kiosk mode (Ctrl+Shift+K)');
  expect(enter?.previousElementSibling?.className).toBe('pb-agg');
});

test('the footer kiosk switch navigates between the two routes', async () => {
  await renderBoard('/production-board/kiosk');

  fireEvent.click(screen.getByRole('switch', { name: 'Kiosk mode' }));
  await act(async () => {});
  expect(window.location.pathname).toBe('/production-board');
  expect(document.querySelector('.pbk-head')).toBeNull();
  expect(
    screen
      .getByRole('switch', { name: 'Kiosk mode' })
      .getAttribute('aria-checked'),
  ).toBe('false');

  fireEvent.click(screen.getByRole('switch', { name: 'Kiosk mode' }));
  await act(async () => {});
  expect(window.location.pathname).toBe('/production-board/kiosk');
  expect(document.querySelector('.pbk-head')).not.toBeNull();
  expect(
    screen
      .getByRole('switch', { name: 'Kiosk mode' })
      .getAttribute('aria-checked'),
  ).toBe('true');
});

test('the standard route keeps the normal header and no kiosk chrome', async () => {
  await renderBoard();

  expect(document.querySelector('.pbk-head')).toBeNull();
  expect(document.querySelector('.pb')?.className).not.toContain('kiosk');
  // The identity group is IDENTICAL to the kiosk header (v17); no
  // brand mark exists anywhere, and no theme control renders in the
  // standard header (the top navigation supplies it there).
  expect(document.querySelector('.pb-head .live')).not.toBeNull();
  expect(document.querySelector('.pbk-brand')).toBeNull();
  expect(document.querySelector('.pb-head .themetoggle')).toBeNull();
  // The footer kiosk switch reads Off — no `Exit kiosk` action text
  // anywhere in the standard presentation.
  expect(
    screen
      .getByRole('switch', { name: 'Kiosk mode' })
      .getAttribute('aria-checked'),
  ).toBe('false');
  expect(document.querySelector('.pb-foot')?.textContent).not.toContain(
    'Exit kiosk',
  );
});

test('Ctrl+Shift+K toggles the kiosk route but stays inert in fields and dialogs', async () => {
  await renderBoard();

  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  await act(async () => {});
  expect(window.location.pathname).toBe('/production-board/kiosk');
  expect(document.querySelector('.pbk-head')).not.toBeNull();

  fireEvent.keyDown(window, { key: 'k', ctrlKey: true, shiftKey: true });
  await act(async () => {});
  expect(window.location.pathname).toBe('/production-board');
  expect(document.querySelector('.pbk-head')).toBeNull();

  // Inert while an application modal dialog is active.
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  document.body.appendChild(dialog);
  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/production-board');
  dialog.remove();

  // Inert inside an unrelated text-entry control.
  const input = document.createElement('input');
  document.body.appendChild(input);
  fireEvent.keyDown(input, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/production-board');
  input.remove();

  // Without the full chord nothing toggles.
  fireEvent.keyDown(window, { key: 'K', ctrlKey: true });
  expect(window.location.pathname).toBe('/production-board');
});

/* ============ Automatic display scaling (auto-fit zoom, v18) ============ */

// jsdom applies no layout and no CSS zoom, so the scaling contract is
// checked structurally (the footer toggle) and at the stylesheet
// level: baseline sizes stay plain pixels (the board recalc applies
// one uniform inline `zoom` computed by board-logic autoFitScale —
// unit-tested in board-logic.test.ts), with no width breakpoints and
// no leftover per-element fluid math.

async function readBoardCss(): Promise<string> {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  return readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
}

/** The rule block for an exact selector, with comments stripped. */
function ruleBlock(css: string, selector: string): string {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return stripped.match(new RegExp(`${escaped}\\s*\\{[^}]*\\}`))?.[0] ?? '';
}

test('the footer slide switch toggles automatic display scaling on and off', async () => {
  await renderBoard();

  // Default ON — the board is a large-display view. The switch uses
  // the same slide-control language as the Machines Maintenance
  // switch (role="switch", track + knob + written state) and sits in
  // the footer controls row directly after the kiosk toggle, never
  // in the header or the legend row.
  const sw = document.querySelector('.pb-foot .pb-footrow .pb-scaleswitch');
  expect(sw).not.toBeNull();
  expect(sw?.getAttribute('role')).toBe('switch');
  expect(sw?.getAttribute('aria-checked')).toBe('true');
  expect(sw?.getAttribute('aria-label')).toBe('Automatic display scaling');
  expect(sw?.classList.contains('on')).toBe(true);
  expect(sw?.querySelector('.track .knob')).not.toBeNull();
  // The state is written text beside the knob — never color-only.
  expect(sw?.querySelector('.swlbl')?.textContent).toBe('Auto scale');
  expect(sw?.querySelector('.swstate')?.textContent).toBe('On');
  expect(sw?.previousElementSibling?.classList.contains('pb-kioskswitch')).toBe(
    true,
  );
  expect(document.querySelector('.pb-head .pb-scaleswitch')).toBeNull();
  expect(
    document.querySelector('.pb-footrow.legend .pb-scaleswitch'),
  ).toBeNull();

  // OFF: state flips in aria, class and written text together.
  fireEvent.click(sw!);
  expect(sw?.getAttribute('aria-checked')).toBe('false');
  expect(sw?.classList.contains('on')).toBe(false);
  expect(sw?.querySelector('.swstate')?.textContent).toBe('Off');

  // And back on.
  fireEvent.click(sw!);
  expect(sw?.getAttribute('aria-checked')).toBe('true');
  expect(sw?.classList.contains('on')).toBe(true);
  expect(sw?.querySelector('.swstate')?.textContent).toBe('On');
});

test('the scale switch exists in both presentations of the board', async () => {
  const view = await renderBoard('/production-board/kiosk');
  expect(
    document
      .querySelector('.pb-foot .pb-footrow .pb-scaleswitch')
      ?.getAttribute('aria-checked'),
  ).toBe('true');
  view.unmount();

  await renderBoard('/production-board');
  expect(
    document
      .querySelector('.pb-foot .pb-footrow .pb-scaleswitch')
      ?.getAttribute('aria-checked'),
  ).toBe('true');
});

test('scaling is one uniform zoom — the stylesheet keeps fixed baseline sizes', async () => {
  const css = await readBoardCss();

  // No per-element fluid font math and no discrete width breakpoints
  // survive: the auto-fit factor is computed from the measured
  // intrinsic table width (board-logic autoFitScale) and applied as
  // ONE inline zoom on the board root, so every text — header, table
  // and footer alike, primary and secondary — scales by the same
  // factor and the visual hierarchy is preserved exactly.
  expect(css).not.toContain('--pb-fluid');
  expect(css).not.toContain('--pb-scale');
  expect(css).not.toMatch(/@media[^{]*min-width/);
  expect(css).not.toMatch(/font-size:\s*calc/);

  // Baseline sizes stay plain pixels at every tier.
  expect(ruleBlock(css, '.pb-table .part')).toContain('font-size: 22px');
  expect(ruleBlock(css, '.pb-table .pname')).toContain('font-size: 13px');
  expect(ruleBlock(css, '.pb-head .clock')).toContain('font-size: 30px');
  expect(ruleBlock(css, '.pb-foot')).toContain('font-size: 13.5px');

  // The hidden measurement copy never overrides sizes: identical
  // classes → identical metrics, so the intrinsic-width measurement
  // and the height-aware pagination stay truthful for the scaled
  // board (the pagination divides its viewport budget by the same
  // scale in the component).
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
  expect(stripped).not.toMatch(/\.pb-measure[^{]*\{[^}]*font-size/);
  expect(stripped).not.toMatch(/\.pb-measure[^{]*\{[^}]*zoom/);

  // The slide switches are board-owned: ONE shared track/knob family
  // (.pb-switch) styled in this stylesheet for BOTH footer toggles
  // (kiosk mode and auto scale), with no selector dependency on the
  // Machines stylesheet (comments may reference it) and the knob
  // travel expressed as a transform like the Maintenance switch the
  // family mirrors.
  expect(stripped).not.toContain('mg-switch');
  expect(ruleBlock(css, '.pb-foot .pb-switch .track')).not.toBe('');
  expect(ruleBlock(css, '.pb-foot .pb-switch.on .knob')).toContain(
    'transform: translateX(',
  );
});

/* ============ The real feed (Phase 11) ============ */

function connectivityTree(status: 'connected' | 'unavailable' | 'connecting') {
  return (
    <ThemeProvider>
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <RouterProvider>
          <ProductionBoardView />
        </RouterProvider>
      </ConnectivityContext.Provider>
    </ThemeProvider>
  );
}

test('the board loads its rows from GET /api/production-board and passes ?department through', async () => {
  const fetchImpl = stubFetch();
  await renderBoard('/production-board?department=7');

  const boardCalls = fetchImpl.mock.calls.filter(([input]) =>
    String(input).startsWith('/api/production-board'),
  );
  expect(boardCalls).toHaveLength(1);
  expect(String(boardCalls[0][0])).toBe(
    '/api/production-board?department_id=7',
  );
  // Rows, the Department line and the footer totals come from the answer.
  expect(rowByPn('2027-60-8114-00')).toBeDefined();
  expect(document.querySelector('.pb-headid .dept')?.textContent).toBe(
    'Machine Shop',
  );
  const agg = document.querySelector('.pb-agg')?.textContent ?? '';
  expect(agg).toContain('6 active PNs');
  expect(agg).toContain('64 pcs in production');
  expect(agg).toContain('50 pcs stocked');
  expect(agg).toContain('3 pcs scrapped');
});

test('without ?department the server resolves the Department (no department_id sent)', async () => {
  const fetchImpl = stubFetch();
  await renderBoard('/production-board');

  const boardCalls = fetchImpl.mock.calls.filter(([input]) =>
    String(input).startsWith('/api/production-board'),
  );
  expect(String(boardCalls[0][0])).toBe('/api/production-board');
});

test('the Job Numbers column names every demand with its WO context', async () => {
  await renderBoard();

  const jobs = Array.from(
    rowByPn('78-04-0031')?.querySelectorAll('.jobs .j') ?? [],
    (el) => el.textContent,
  );
  expect(jobs).toEqual([
    '18102 · WO 007002 · 6 pcs',
    // Internal MODIFY demand: no Job Number and no external WO Number.
    '— · WO — · MODIFY · 1 pc',
  ]);
  const stockedJobs = Array.from(
    rowByPn('309-127')?.querySelectorAll('.jobs .j') ?? [],
    (el) => el.textContent,
  );
  expect(stockedJobs).toEqual(['17740 · WO 006996 · allocated 50/50']);
});

test('the first load shows the loading state under the board header', async () => {
  let resolveBoard: (response: Response) => void = () => {};
  stubFetch(
    () =>
      new Promise<Response>((resolve) => {
        resolveBoard = resolve;
      }),
  );
  await renderBoard();

  // The header (title + clock) is present while the table area loads.
  expect(
    screen.getByRole('status', { name: 'Loading Production Board' }),
  ).toBeInTheDocument();
  expect(document.querySelector('.pb-head h1.live')).not.toBeNull();
  expect(document.querySelector('table.pb-table')).toBeNull();

  await act(async () => {
    resolveBoard(new Response(JSON.stringify(boardPayload()), { status: 200 }));
  });
  expect(rowByPn('2027-60-8114-00')).toBeDefined();
  expect(
    screen.queryByRole('status', { name: 'Loading Production Board' }),
  ).toBeNull();
});

test('a failed first load is the error state with Retry; Retry reloads', async () => {
  let fail = true;
  stubFetch(() =>
    Promise.resolve(
      fail
        ? new Response(
            JSON.stringify({ detail: 'Department 7 does not exist.' }),
            {
              status: 404,
            },
          )
        : new Response(JSON.stringify(boardPayload()), { status: 200 }),
    ),
  );
  await renderBoard('/production-board?department=7');

  const alert = screen.getByRole('alert');
  expect(alert.textContent).toContain(
    'The production feed could not be loaded.',
  );
  expect(alert.textContent).toContain('Department 7 does not exist.');
  expect(document.querySelector('table.pb-table')).toBeNull();
  // The footer totals are not claimed before a first complete answer.
  expect(document.querySelector('.pb-agg')).toBeNull();

  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(screen.queryByRole('alert')).toBeNull();
  expect(rowByPn('2027-60-8114-00')).toBeDefined();
});

test('the board refreshes itself periodically and a failed refresh marks the feed stale without dropping the rows', async () => {
  let answer: 'ok' | 'fail' = 'ok';
  const fetchImpl = stubFetch(() =>
    Promise.resolve(
      answer === 'ok'
        ? new Response(JSON.stringify(boardPayload()), { status: 200 })
        : new Response(JSON.stringify({ detail: 'boom' }), { status: 500 }),
    ),
  );
  await renderBoard();
  const boardCalls = () =>
    fetchImpl.mock.calls.filter(([input]) =>
      String(input).startsWith('/api/production-board'),
    ).length;
  expect(boardCalls()).toBe(1);

  // One request per refresh period, armed after the previous answer.
  await act(async () => {
    vi.advanceTimersByTime(BOARD_REFRESH_MS);
  });
  expect(boardCalls()).toBe(2);
  const live = () => document.querySelector('.pb-head h1.live');
  expect(live()?.className).not.toContain('stale');

  // The next refresh fails: the rows stay, the title turns stale with
  // the explicit note — never an empty board, never an error page.
  answer = 'fail';
  await act(async () => {
    vi.advanceTimersByTime(BOARD_REFRESH_MS);
  });
  expect(boardCalls()).toBe(3);
  expect(rowByPn('2027-60-8114-00')).toBeDefined();
  expect(live()?.className).toContain('stale');
  expect(live()?.querySelector('.stalenote')?.textContent).toBe(
    'Feed stale — reconnecting',
  );
  expect(screen.queryByRole('alert')).toBeNull();

  // Polling continues; the next good answer clears the stale flag.
  answer = 'ok';
  await act(async () => {
    vi.advanceTimersByTime(BOARD_REFRESH_MS);
  });
  expect(boardCalls()).toBe(4);
  expect(live()?.className).not.toContain('stale');
  expect(live()?.querySelector('.stalenote')).toBeNull();
});

test('lost connectivity shows the stale feed on the loaded rows, and its return refreshes at once', async () => {
  const fetchImpl = stubFetch();
  window.history.replaceState({}, '', '/production-board');
  const view = render(connectivityTree('connected'));
  await act(async () => {});
  const boardCalls = () =>
    fetchImpl.mock.calls.filter(([input]) =>
      String(input).startsWith('/api/production-board'),
    ).length;
  expect(boardCalls()).toBe(1);
  expect(rowByPn('2027-60-8114-00')).toBeDefined();

  view.rerender(connectivityTree('unavailable'));
  const live = document.querySelector('.pb-head h1.live');
  expect(live?.className).toContain('stale');
  expect(live?.querySelector('.stalenote')?.textContent).toBe(
    'Feed stale — reconnecting',
  );
  // The loaded rows stay on screen.
  expect(rowByPn('2027-60-8114-00')).toBeDefined();

  // Connectivity back: an immediate refresh, not a wait for the period.
  view.rerender(connectivityTree('connected'));
  await act(async () => {});
  expect(boardCalls()).toBe(2);
  expect(document.querySelector('.pb-head h1.live')?.className).not.toContain(
    'stale',
  );
});

test('a Department with nothing in production renders the empty state with the header and footer', async () => {
  stubFetch(() =>
    Promise.resolve(
      new Response(JSON.stringify(boardPayload([])), { status: 200 }),
    ),
  );
  await renderBoard();

  expect(
    screen.getByText('No active production in this Department.'),
  ).toBeInTheDocument();
  expect(document.querySelector('.pb-headid .dept')?.textContent).toBe(
    'Machine Shop',
  );
  expect(document.querySelector('.pb-agg')?.textContent).toContain(
    '0 active PNs',
  );
  expect(screen.getByText('Page 1 / 1')).toBeInTheDocument();
});

test('the development state previews perform no board request', async () => {
  const fetchImpl = stubFetch();
  await renderBoard('/production-board?state=long');
  expect(
    fetchImpl.mock.calls.filter(([input]) =>
      String(input).startsWith('/api/production-board'),
    ),
  ).toHaveLength(0);
  expect(rowByPn('0118-40-0022-07-0455-88-REV-C')).toBeDefined();
});
