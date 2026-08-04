import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityProvider } from '../../app/connectivity-provider';
import { ProductionBoardView } from './ProductionBoardView';

// Production Board regressions: column order and content-driven sizing
// markup, the standard Hot presentation, urgency-only blinking, aligned
// Areas & Quantities markup with the explicit location-state model
// (machine chip / queue / processing / done / stocked), scrap on its
// own line, canonical row ordering with nullable due dates, the live
// clock, and height-aware pagination + rotation. jsdom has no layout,
// so sizing rules are asserted through classes/attributes/structure —
// never computed pixel widths.

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
      ),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function renderBoard(path = '/production-board') {
  window.history.replaceState({}, '', path);
  const view = render(
    <ConnectivityProvider>
      <ProductionBoardView />
    </ConnectivityProvider>,
  );
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

  const headers = Array.from(
    visibleTable().querySelectorAll('thead th'),
    (th) => th.textContent,
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

test('the footer legend describes the No.-column flame, not 🔥#n before the PN', async () => {
  await renderBoard();

  const foot = document.querySelector('.pb-foot');
  expect(foot?.textContent).toContain('🔥 in the No. column = Hot priority');
  expect(foot?.textContent).not.toContain('before the PN');
  expect(foot?.textContent).not.toContain('⊘');
});

test('the footer states the sorting rule in user language', async () => {
  await renderBoard();

  const foot = document.querySelector('.pb-foot');
  expect(foot?.textContent).toContain(
    'Order: Hot rank first → earliest due date → no due date by oldest received date.',
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
  // …legend items (flame, blink, dashes, sorting) in the second.
  const legends = Array.from(rows[1].querySelectorAll('.leg'), (el) =>
    el.textContent?.trim(),
  );
  expect(legends).toHaveLength(4);
  expect(legends[0]).toContain('🔥 in the No. column');
  expect(legends[3]).toContain('Order: Hot rank first');
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
  // is: shared minimum track widths (minmax + max-content growth) for
  // Location | Quantity | State | Time, with a trailing 1fr spacer so
  // time stays near its location data instead of the far cell edge.
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'production-board.css'),
    'utf8',
  );
  const loc = css.match(/\.pb-table \.loc \{[^}]*\}/)?.[0] ?? '';
  expect(loc).toContain('grid-template-columns');
  expect(loc.match(/minmax\(\d+ch, max-content\)/g)?.length).toBe(4);
  expect(loc).toMatch(/1fr;/);
  // Explicit track assignment keeps every row inside the shared tracks.
  expect(css).toMatch(/\.locrow \.lname \{[^}]*grid-column: 1/);
  expect(css).toMatch(/\.locrow \.ltime \{[^}]*grid-column: 4/);
});

test('the clock reads time-first with the date as its secondary line', async () => {
  await renderBoard();

  const wrap = document.querySelector('.pb-head .clockwrap');
  const children = Array.from(wrap?.children ?? [], (el) => el.className);
  expect(children).toEqual(['clock', 'clockdate']);
});

test('rows follow canonical order: Hot → dated → undated → stocked', async () => {
  await renderBoard();

  const parts = Array.from(
    visibleTable().querySelectorAll('tbody .part'),
    (el) => el.textContent,
  );
  expect(parts).toEqual([
    '2027-60-8114-00',
    '142-260',
    '0455-20-0118-03',
    '0123-40-0007-22',
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

test('active Machine rows render dot + machine chip + qty + `on mch.` + time', async () => {
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
  expect(machineRow?.querySelector('.ltag')?.textContent).toBe('on mch.');
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
  // `on mch.`.
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
  expect(doneRow?.textContent).not.toContain('on mch.');

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

test('scrap renders as a chip on the total line, right-anchored, no ⊘', async () => {
  await renderBoard();

  const loc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  // The chip lives inside the total row's right-hand time cell — the
  // same line as `total … pcs`, never an extra row.
  const chip = loc?.querySelector('.locrow.total .ltime .scrapchip');
  expect(chip?.textContent).toBe('1 scrapped');
  expect(loc?.querySelector('.locrow.scrap')).toBeNull();
  expect(loc?.textContent).not.toContain('⊘');

  // Multi-piece wording comes straight from the value.
  const platedLoc = rowByPn('142-260')?.querySelector('.loc');
  expect(
    platedLoc?.querySelector('.locrow.total .ltime .scrapchip')?.textContent,
  ).toBe('2 scrapped');

  // Zero scrap renders no chip at all.
  const cleanLoc = rowByPn('118-052')?.querySelector('.loc');
  expect(cleanLoc?.querySelector('.scrapchip')).toBeNull();
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
    'on mch.',
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
  expect(css).toContain('@keyframes pb-live-pulse');
  expect(css).toContain('animation: pb-live-pulse');
  expect(css).not.toContain('ss-pulse');
  expect(css).toContain('prefers-reduced-motion');
  expect(css).toMatch(/\.live\.stale \.ld \{[^}]*animation: none/);
  expect(css).not.toMatch(/\.mchip \{[^}]*text-overflow/);
  expect(css).not.toMatch(/\.lname \{[^}]*text-overflow/);
  // Intrinsic 15ch PN minimum instead of an arbitrary pixel width.
  expect(css).toMatch(/\.part \{[^}]*min-width: 15ch/);
  expect(css).not.toContain('min-width: 310px');
});

test('the Live indicator distinguishes connected and stale states', async () => {
  await renderBoard();
  const live = document.querySelector('.pb-head .live');
  expect(live?.textContent).toBe('Live');
  expect(live?.className).not.toContain('stale');
  expect(live?.querySelector('.ld')).not.toBeNull();
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
  expect(
    screen.getByText(/Page 1 \/ 3 · rotates every 12 s/),
  ).toBeInTheDocument();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(12_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(24_000);
  });
  // Wraps around after the last page.
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

test('manual navigation restarts the auto-rotation timer', async () => {
  await renderBoard('/production-board?state=long');

  // 8 s into the cycle a manual navigation happens…
  await act(async () => {
    await vi.advanceTimersByTimeAsync(8_000);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // …so 8 s later (16 s from the old timer's start) nothing rotates
  // yet — the timer restarted at the manual change.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(8_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // The full interval after the manual change, rotation resumes.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4_000);
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
    await vi.advanceTimersByTimeAsync(8_000);
  });
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(8_000);
  });
  // Timer restarted by the arrow navigation.
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // After unmount the window listener is removed — no state updates,
  // no act() warnings, no errors.
  view.unmount();
  fireEvent.keyDown(window, { key: 'ArrowRight' });
});

test('the active page clamps when the page structure changes', async () => {
  const view = await renderBoard('/production-board?state=long');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(12_000);
  });
  expect(screen.getByText(/Page 2 \/ 3/)).toBeInTheDocument();

  // The data set shrinks to a single page: the page indicator resets
  // and no rotation is claimed.
  window.history.replaceState({}, '', '/production-board');
  view.rerender(
    <ConnectivityProvider>
      <ProductionBoardView />
    </ConnectivityProvider>,
  );
  await act(async () => {});
  expect(screen.getByText('Page 1 / 1')).toBeInTheDocument();
  expect(screen.queryByText(/rotates every/)).toBeNull();
});
