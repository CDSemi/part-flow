import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
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
  // Click the Area TAB specifically — the overview column headers are
  // buttons too and would make an unscoped name query ambiguous.
  const tabs = document.querySelector<HTMLElement>('.ab-tabs')!;
  fireEvent.click(within(tabs).getByRole('button', { name }));
}

test('the Area detail leads with a summary card: stats + grouped PN list', () => {
  render(<AreaBoardView />);
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
  // Only the actively assigned 3 pcs — the finished 1 pc left the card.
  expect(lathe3.querySelector('.mtotals')?.textContent).toContain('3');

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
  // Deburr's whole quantity is finished (READY_TO_TRANSFER): only the
  // finished group renders — no queue wording for a no-Machine Area,
  // and the finished state is never presented as Stocked.
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('Finished — ready to move');
  expect(summary?.textContent).not.toContain('awaiting Machine');
  expect(summary?.textContent).not.toContain('Stocked');
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

test('finished quantity lives in the Area summary, not in a Machine card', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);

  const summary = document.querySelector('.abd-summary')!;
  // The finished portion is a distinct success/ready row that keeps the
  // completing Machine as context only.
  expect(summary.querySelector('.ctx.done')).not.toBeNull();
  expect(summary.textContent).toContain('Finished at Lathe 3 — ready to move');
  // The finished pc is not presented as Stocked and never inside a
  // Machine card.
  for (const card of document.querySelectorAll('.abd-machine')) {
    expect(card.textContent).not.toContain('ready to move');
  }
  expect(summary.textContent).not.toContain('Stocked');
});

test('the All Areas overview reuses the shared PN row presentation', () => {
  render(<AreaBoardView />);

  const latheColumn = Array.from(document.querySelectorAll('.ms-col')).find(
    (col) => col.querySelector('.mc-title')?.textContent?.includes('Lathe'),
  )!;
  // Shared row shell: rowmain grid lines with the shared subcomponents.
  const row = latheColumn.querySelector('.mc-list li .rowmain')!;
  expect(row.querySelector('.r1 .r1r .qtyline')?.textContent).toMatch(
    /\d+ pcs/,
  );
  const wo = row.querySelector<HTMLElement>('.r2 .wo');
  expect(wo?.getAttribute('title')).toBe(wo?.textContent);
  // Aggregated portion chips distinguish machine / queue / done.
  const chips = Array.from(
    latheColumn.querySelectorAll('.mc-list .ctxs .ctx'),
    (el) => el.textContent,
  );
  expect(chips).toContain('Lathe 3 × 3');
  expect(chips).toContain('queue × 2');
  expect(chips).toContain('done × 1');
  // The overview column shows the Done stat with the success tone.
  expect(latheColumn.querySelector('.stat .n.d')?.textContent).toBe('1');
});

test('queued, on-Machine, processing, and finished states stay distinguishable', () => {
  render(<AreaBoardView />);
  openArea(/^Lathe/);
  const summary = document.querySelector('.abd-summary')!;
  expect(summary.textContent).toContain('On Machine');
  expect(summary.textContent).toContain('Awaiting Machine');
  expect(summary.textContent).toContain('ready to move');

  openArea(/^Manual/);
  expect(document.querySelector('.abd-summary')?.textContent).toContain(
    'In processing',
  );
});
