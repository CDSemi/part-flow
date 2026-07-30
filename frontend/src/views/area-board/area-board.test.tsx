import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';

import { AreaBoardView } from './AreaBoardView';

// Area Board regressions: the redesigned per-Area detail (Area summary
// card + one monitoring card per Machine), quantity-preserving
// grouping, nullable due dates, and the working Time in Area sort.

beforeEach(() => {
  window.history.replaceState({}, '', '/management/area-board');
});

afterEach(cleanup);

function openArea(name: RegExp) {
  fireEvent.click(screen.getByRole('button', { name }));
}

test('the Area detail leads with a summary card: stats + grouped PN list', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);

  const cards = Array.from(document.querySelectorAll('.abd-card'));
  expect(cards.length).toBe(5); // 1 Area summary + 4 Lathe machines
  const summary = cards[0];
  expect(summary.className).toContain('abd-summary');
  expect(summary.textContent).toContain('Turning');

  // Statistics: 3 PNs · 12 pcs total · 4 queued · 8 on machines · 1 Hot.
  const stats = Array.from(
    summary.querySelectorAll('.stat .n'),
    (el) => el.textContent,
  );
  expect(stats).toEqual(['3', '12', '4', '8', '1']);

  // Grouping: assigned first, then the Area queue.
  const groups = Array.from(
    summary.querySelectorAll('.abd-grp'),
    (el) => el.textContent,
  );
  expect(groups).toEqual([
    'Assigned to Machines',
    'Area queue — awaiting Machine',
  ]);

  // Quantities are neither duplicated nor lost by the grouping.
  const quantities = Array.from(summary.querySelectorAll('.mc-list .q'), (el) =>
    Number(el.textContent),
  );
  expect(quantities.reduce((s, q) => s + q, 0)).toBe(12);
});

test('each Machine gets a monitoring card; inactive Machines stay distinct', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);

  const machineCards = Array.from(document.querySelectorAll('.abd-machine'));
  expect(
    machineCards.map((card) => card.querySelector('.mname')?.textContent),
  ).toEqual(['Lathe 1', 'Lathe 2', 'Lathe 3', 'Lathe 4']);

  const lathe3 = machineCards[2];
  expect(lathe3.querySelector('.mstat')?.textContent).toBe('running');
  expect(lathe3.textContent).toContain('2027-60-8114-00');
  expect(lathe3.querySelector('.mtotals')?.textContent).toContain('4');

  const lathe1 = machineCards[0];
  expect(lathe1.querySelector('.mstat')?.textContent).toBe('idle');
  expect(lathe1.textContent).toContain('No production assigned');

  const lathe4 = machineCards[3];
  expect(lathe4.className).toContain('maintenance');
  expect(lathe4.textContent).toContain(
    'Under maintenance — accepts no production',
  );
});

test('Areas without Machines render only the summary card — no placeholders', () => {
  render(<AreaBoardView />);
  openArea(/^Manual/);

  const cards = document.querySelectorAll('.abd-card');
  expect(cards.length).toBe(1);
  // The undated WO Demand displays its missing due date as valid data.
  expect(cards[0].textContent).toContain('118-052');
  expect(cards[0].textContent).toContain('No due date');
});

test('search still narrows the detail PN lists', () => {
  render(<AreaBoardView />);
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

test('Sort: Time in Area orders by the stored sortable duration', () => {
  render(<AreaBoardView />);
  fireEvent.change(screen.getByLabelText('Sort'), {
    target: { value: 'tia' },
  });

  // Lathe column (All Areas overview): 214-406 (6h 12m) has waited the
  // longest, then 2027-60-8114-00 (2h 05m), then 0455-20-0118-03 (1h 05m).
  const latheColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  );
  expect(latheColumn).toBeDefined();
  const pns = Array.from(
    latheColumn!.querySelectorAll('.mc-list .p'),
    (el) => el.textContent,
  );
  expect(pns).toEqual(['214-406', '2027-60-8114-00', '0455-20-0118-03']);
});

test('Sort: Due date applies canonical order — undated demands last', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);

  // Hot #1 (2027…) leads, then dated by due date, undated after dated.
  const summary = document.querySelector('.abd-summary');
  const pns = Array.from(
    summary!.querySelectorAll('.mc-list .p'),
    (el) => el.textContent,
  );
  expect(pns[0]).toBe('2027-60-8114-00');
});

test('the detail uses the shared [summary | machine grid] layout, read-only', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);

  // Shared structural layout: left summary column + right Machine grid.
  const layout = document.querySelector('.am');
  expect(layout).not.toBeNull();
  expect(layout?.classList.contains('am-single')).toBe(false);
  expect(document.querySelectorAll('.am-machines .abd-machine').length).toBe(4);
  // Area Board stays read-only — no Scan Station action buttons and no
  // action rail cells at all.
  expect(screen.queryByRole('button', { name: 'ASSIGN' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'QUEUE' })).toBeNull();
  expect(document.querySelector('.mc-list .actcell')).toBeNull();
});

test('a no-Machine Area renders the full-width single-column layout', () => {
  render(<AreaBoardView />);
  openArea(/^Deburr/);

  const layout = document.querySelector('.am');
  expect(layout?.classList.contains('am-single')).toBe(true);
  expect(document.querySelector('.abd-machine')).toBeNull();
  // Direct processing group — no queue wording for a no-Machine Area.
  expect(document.querySelector('.abd-summary')?.textContent).toContain(
    'In processing',
  );
});

test('scrap quantities appear in the PN summaries as text only', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('1 scrapped');
  // Scrap is never displayed twice: the compact ⊘ indicator is gone.
  expect(summary?.textContent).not.toContain('⊘');
  expect(document.querySelector('.mc-list .scrap')).toBeNull();
  // The blank-number internal MODIFY Work Order renders as `—`.
  expect(summary?.textContent).toContain('WO —');
});
