import { act, cleanup, render, screen } from '@testing-library/react';
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

test('the PN cell carries the min-width/nowrap hook for the standard 15-char PN', async () => {
  await renderBoard();

  // `.pb-table th.pn / td.pn` own the CSS min-width + nowrap that keep
  // `🔥#1 2027-60-8114-00` on one line; the class/structure is the
  // regression surface (jsdom cannot measure the pixel width).
  const th = visibleTable().querySelector('th.pn');
  expect(th?.textContent).toBe('Part Number');
  const cell = rowByPn('2027-60-8114-00')?.querySelector('td.pn');
  expect(cell).toBeDefined();
  const part = cell?.querySelector('.part');
  expect(part?.textContent).toBe('🔥#1 2027-60-8114-00');
});

test('Hot Parts render as 🔥#n immediately before the PN, no chip after', async () => {
  await renderBoard();

  const firstPart = visibleTable().querySelector('tbody .part');
  expect(firstPart?.textContent).toBe('🔥#1 2027-60-8114-00');
});

test('rows follow canonical order: Hot → dated → undated → stocked', async () => {
  await renderBoard();

  const parts = Array.from(
    visibleTable().querySelectorAll('tbody .part'),
    (el) => el.textContent,
  );
  expect(parts).toEqual([
    '🔥#1 2027-60-8114-00',
    '🔥#2 142-260',
    '0455-20-0118-03',
    '0123-40-0007-22',
    '78-04-0031',
    '118-052',
    '309-127',
  ]);
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
  // The scrap line deliberately spans the grid instead of using the
  // four aligned columns.
  const rows = Array.from(firstLoc!.querySelectorAll('.locrow:not(.scrap)'));
  expect(rows.length).toBeGreaterThan(1);
  rows.forEach((row) => {
    expect(row.querySelector('.lname')).not.toBeNull();
    expect(row.querySelector('.lqty')).not.toBeNull();
    expect(row.querySelector('.ltag')).not.toBeNull();
    expect(row.querySelector('.ltime')).not.toBeNull();
  });
  // The `total … pcs` line uses the same quantity column.
  const total = firstLoc!.querySelector('.locrow.total');
  expect(total?.querySelector('.lqty')?.textContent).toBe('10');
  expect(total?.querySelector('.ltag')?.textContent).toBe('pcs');
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

test('scrapped quantity renders on its own line, separate from the time column', async () => {
  await renderBoard();

  const loc = rowByPn('2027-60-8114-00')?.querySelector('.loc');
  const scrap = loc?.querySelector('.locrow.scrap .lscrap');
  expect(scrap?.textContent).toBe('⊘ 1 scrapped');
  // The scrap value never sits inside the time track…
  expect(scrap?.closest('.ltime')).toBeNull();
  // …and the total row's time cell stays empty of scrap text.
  const totalTime = loc?.querySelector('.locrow.total .ltime');
  expect(totalTime?.textContent).toBe('');
  // A PN without scrap renders no scrap line at all.
  const cleanLoc = rowByPn('118-052')?.querySelector('.loc');
  expect(cleanLoc?.querySelector('.locrow.scrap')).toBeNull();
});

test('a long Machine name exposes the full name through the chip title', async () => {
  await renderBoard('/production-board?state=long');

  const chip = Array.from(visibleTable().querySelectorAll('.mchip')).find(
    (el) => el.textContent === 'Mill 3 — Horizontal Boring',
  );
  expect(chip).toBeDefined();
  // Truncation is CSS-only (ellipsis); the full name must stay
  // reachable via the tooltip.
  expect(chip?.getAttribute('title')).toBe('Mill 3 — Horizontal Boring');
  expect(chip?.closest('.locrow')?.querySelector('.ltag')?.textContent).toBe(
    'on mch.',
  );
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
