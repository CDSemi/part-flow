import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityProvider } from '../../app/connectivity-provider';
import { ProductionBoardView } from './ProductionBoardView';

// Production Board regressions: column order and content-driven sizing
// markup, the standard Hot presentation, urgency-only blinking, aligned
// Areas & Quantities markup, canonical row ordering with nullable due
// dates, the live clock, and height-aware pagination + rotation.

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
  // percentage widths; Job Numbers absorbs the remaining width.
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

  const undatedRow = Array.from(
    visibleTable().querySelectorAll('tbody tr'),
  ).find((tr) => tr.textContent?.includes('118-052'));
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

test('Areas & Quantities rows share aligned label/qty/tag/time columns', async () => {
  await renderBoard();

  const firstLoc = visibleTable().querySelector('tbody .loc');
  expect(firstLoc).not.toBeNull();
  const rows = Array.from(firstLoc!.querySelectorAll('.locrow'));
  expect(rows.length).toBeGreaterThan(1);
  rows.forEach((row) => {
    expect(row.querySelector('.lname')).not.toBeNull();
    expect(row.querySelector('.lqty')).not.toBeNull();
    expect(row.querySelector('.ltime')).not.toBeNull();
  });
  // The `total … pcs` line uses the same quantity column.
  const total = firstLoc!.querySelector('.locrow.total');
  expect(total?.querySelector('.lqty')?.textContent).toBe('10');
  expect(total?.querySelector('.ltag')?.textContent).toBe('pcs');
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
