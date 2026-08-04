import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityProvider } from '../../app/connectivity-provider';
import { PriorityView } from './PriorityView';

// Priority regressions: every operation that reorders existing Hot
// entries (drag and drop, Move Up, Move Down, Undo, Redo) requires
// confirmation before applying; the confirmation summarizes the moved
// item and compares current versus proposed ranks for every affected
// entry; cancelling leaves the list, the undo history and the redo
// history unchanged; the visible list is never renumbered before
// confirmation.

beforeEach(() => {
  window.history.replaceState({}, '', '/management/priority');
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
});

async function renderPriority() {
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
  );
  // Wait for the connectivity check so reorder controls enable.
  await screen.findByRole('button', { name: '⟲ Undo' });
  await vi.waitFor(() =>
    expect(
      screen.getByRole('button', {
        name: 'Move 2027-60-8114-00 down',
      }),
    ).toBeEnabled(),
  );
}

function listedPns(): (string | null)[] {
  return Array.from(
    document.querySelectorAll('.pr-item .pn'),
    (el) => el.textContent,
  );
}

const INITIAL = ['2027-60-8114-00', '142-260', '309-127'];

test('Move Down asks for confirmation; Cancel and Escape change nothing', async () => {
  await renderPriority();
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );

  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  // Primary summary of the moved item, plus the action as detail.
  expect(dialog).toHaveTextContent(
    'Move 2027-60-8114-00 · WO 007001 from #1 to #2',
  );
  expect(dialog).toHaveTextContent('1 other demand will shift up.');
  expect(dialog).toHaveTextContent('Move Down');
  // New Position rows read as complete `#old → #new` transitions.
  const newSide = dialog.querySelectorAll('.pr-snapshot')[1];
  const transitions = Array.from(
    newSide.querySelectorAll('.pr-snaprow .prr'),
    (el) => el.textContent,
  );
  expect(transitions).toEqual(['#2 → #1', '#1 → #2']);
  // The visible list is not renumbered before confirmation.
  expect(listedPns()).toEqual(INITIAL);

  // Escape closes without applying — matching the "Cancel (Esc)" label.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(
    screen.queryByRole('dialog', { name: 'Confirm Hot ranking change' }),
  ).toBeNull();
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeDisabled();

  // The Cancel button behaves the same: list and both histories unchanged.
  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeDisabled();
});

test('Move Down applies only after confirmation', async () => {
  await renderPriority();

  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));

  expect(listedPns()).toEqual(['142-260', '2027-60-8114-00', '309-127']);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeEnabled();
});

test('Move Up asks for confirmation and applies on confirm', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: 'Move 309-127 up' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Move Up');
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));

  expect(listedPns()).toEqual(['2027-60-8114-00', '309-127', '142-260']);
});

test('drag and drop asks for confirmation; cancel changes nothing', async () => {
  await renderPriority();
  const items = document.querySelectorAll('.pr-item');

  fireEvent.dragStart(items[0]);
  fireEvent.dragOver(items[2]);
  fireEvent.drop(items[2]);

  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Drag and drop');
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeDisabled();

  // The same drop applies after confirmation.
  const itemsAgain = document.querySelectorAll('.pr-item');
  fireEvent.dragStart(itemsAgain[0]);
  fireEvent.drop(itemsAgain[2]);
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  expect(listedPns()).toEqual(['142-260', '309-127', '2027-60-8114-00']);
});

test('confirmation shows Current Position and New Position snapshots', async () => {
  await renderPriority();
  const items = document.querySelectorAll('.pr-item');

  // Drop the first entry at the bottom: #1 → #3, two entries shift up.
  fireEvent.dragStart(items[0]);
  fireEvent.dragOver(items[2]);
  fireEvent.drop(items[2]);

  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent(
    'Move 2027-60-8114-00 · WO 007001 from #1 to #3',
  );
  expect(dialog).toHaveTextContent('2 other demands will shift up.');

  // Two snapshot sections with exactly one transition arrow between.
  const sections = dialog.querySelectorAll('.pr-snapshot');
  expect(sections).toHaveLength(2);
  const [current, proposed] = Array.from(sections);
  expect(current.querySelector('.pr-snaptitle')?.textContent).toBe(
    'Current Position',
  );
  expect(proposed.querySelector('.pr-snaptitle')?.textContent).toBe(
    'New Position',
  );
  expect(dialog.querySelectorAll('.pr-transition')).toHaveLength(1);

  // Current Position: rows in current rank order, rank first, per-row
  // direction arrows, the moved item highlighted.
  const curRows = Array.from(current.querySelectorAll('.pr-snaprow'));
  expect(curRows).toHaveLength(3);
  expect(curRows[0].querySelector('.prr')?.textContent).toContain('#1');
  expect(curRows[0].querySelector('.prpn')?.textContent).toBe(
    '2027-60-8114-00',
  );
  expect(curRows[0].className).toContain('moved');
  expect(curRows[0].querySelector('.dir.down')?.textContent).toBe('↓');
  expect(curRows[1].querySelector('.dir.up')?.textContent).toBe('↑');
  expect(curRows[2].querySelector('.dir.up')?.textContent).toBe('↑');
  // Rank renders before the PN inside the row.
  const rowChildren = Array.from(curRows[0].children).map((el) => el.className);
  expect(rowChildren[0]).toContain('prr');
  expect(rowChildren[1]).toContain('prpn');

  // PN and WO/Job metadata are visually separate: the chip carries the
  // explicit Work Order and Job Number fields, the PN element does not.
  expect(curRows[0].querySelector('.wjchip')?.textContent).toBe(
    'WO 007001 · Job 18112',
  );
  expect(curRows[0].querySelector('.prpn')?.textContent).not.toContain('WO');

  // New Position: proposed rank order, no per-row direction arrows —
  // every row shows its complete rank transition `#old → #new`.
  const newRows = Array.from(proposed.querySelectorAll('.pr-snaprow'));
  expect(newRows).toHaveLength(3);
  expect(newRows[0].querySelector('.prpn')?.textContent).toBe('142-260');
  expect(newRows[0].querySelector('.prr')?.textContent).toBe('#2 → #1');
  expect(newRows[1].querySelector('.prr')?.textContent).toBe('#3 → #2');
  expect(newRows[2].querySelector('.prpn')?.textContent).toBe(
    '2027-60-8114-00',
  );
  expect(newRows[2].querySelector('.prr')?.textContent).toBe('#1 → #3');
  expect(proposed.querySelector('.dir')).toBeNull();
  // The Current Position side keeps plain ranks (no transitions).
  expect(curRows[0].querySelector('.prr')?.textContent).not.toContain('→');

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(INITIAL);
});

test('Undo and Redo confirm with user-facing titles; cancel preserves both histories', async () => {
  await renderPriority();

  // Create one confirmed change so Undo becomes available.
  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  const reordered = ['142-260', '2027-60-8114-00', '309-127'];
  expect(listedPns()).toEqual(reordered);

  // Undo: cancel first — list, undo history and redo history unchanged.
  fireEvent.click(screen.getByRole('button', { name: '⟲ Undo' }));
  let dialog = screen.getByRole('dialog', {
    name: 'Restore previous ranking',
  });
  // The restore is the primary message; the action name is detail only,
  // and the same rank comparison is shown for the restore.
  expect(dialog).toHaveTextContent('previous confirmed order');
  expect(dialog).toHaveTextContent('Undo');
  expect(dialog.querySelector('.pr-snaprow.moved')).toBeNull();
  // Both entries appear in both snapshots, all as indirect shifts.
  expect(dialog.querySelectorAll('.pr-snaprow.shifted')).toHaveLength(4);
  expect(dialog).toHaveTextContent('#1');
  expect(dialog).toHaveTextContent('#2');
  expect(dialog).toHaveTextContent('↑');
  expect(dialog).toHaveTextContent('↓');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(reordered);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeDisabled();

  // Undo: confirm — the previous ranking is restored.
  fireEvent.click(screen.getByRole('button', { name: '⟲ Undo' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeEnabled();

  // Redo: cancel keeps everything; confirm re-applies.
  fireEvent.click(screen.getByRole('button', { name: '⟳ Redo' }));
  dialog = screen.getByRole('dialog', { name: 'Reapply ranking' });
  expect(dialog).toHaveTextContent('applied again');
  expect(dialog).toHaveTextContent('Redo');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeEnabled();

  fireEvent.click(screen.getByRole('button', { name: '⟳ Redo' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  expect(listedPns()).toEqual(reordered);
});

test('removing an entry still requires its own confirmation', async () => {
  await renderPriority();

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove 309-127 from Hot list' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Remove from Hot list?' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove 309-127 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  expect(listedPns()).toEqual(['2027-60-8114-00', '142-260']);
});

test('Undo restores an entry removed from the bottom of the list', async () => {
  await renderPriority();

  // Removing the last entry shifts no surviving rank — the Undo restore
  // must still be offered and show the entry re-entering at its rank.
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove 309-127 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  expect(listedPns()).toEqual(['2027-60-8114-00', '142-260']);

  fireEvent.click(screen.getByRole('button', { name: '⟲ Undo' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Restore previous ranking',
  });
  // The entry does not exist on the current side: a clear `Not listed`
  // placeholder — never a silent omission; the new side shows #3.
  const [current, proposed] = Array.from(
    dialog.querySelectorAll('.pr-snapshot'),
  );
  const absent = current.querySelector('.pr-snaprow.absent');
  expect(absent).toHaveTextContent('Not listed');
  expect(absent).toHaveTextContent('309-127');
  // The restored entry's transition names both sides explicitly.
  const restored = Array.from(proposed.querySelectorAll('.pr-snaprow')).find(
    (row) => row.textContent?.includes('309-127'),
  );
  expect(restored?.querySelector('.prr')?.textContent).toBe('Not listed → #3');
  expect(proposed.querySelector('.pr-snaprow.absent')).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeEnabled();

  // Redo (remove again): the entry leaves the list — the new side shows
  // the `Not listed` placeholder instead.
  fireEvent.click(screen.getByRole('button', { name: '⟳ Redo' }));
  const redoDialog = screen.getByRole('dialog', { name: 'Reapply ranking' });
  const [redoCurrent, redoProposed] = Array.from(
    redoDialog.querySelectorAll('.pr-snapshot'),
  );
  expect(redoCurrent).toHaveTextContent('#3');
  expect(redoCurrent.querySelector('.pr-snaprow.absent')).toBeNull();
  // The removed entry keeps its origin rank: `#3 → Not listed`.
  expect(
    redoProposed.querySelector('.pr-snaprow.absent .prr')?.textContent,
  ).toBe('#3 → Not listed');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(listedPns()).toEqual(INITIAL);
});

test('adding a new Hot entry at the bottom needs no reorder confirmation', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  fireEvent.click(screen.getByRole('button', { name: /0455-20-0118-03/ }));

  // Applied directly — existing ranks did not change.
  expect(
    screen.queryByRole('dialog', { name: 'Confirm Hot ranking change' }),
  ).toBeNull();
  expect(listedPns()).toEqual([...INITIAL, '0455-20-0118-03']);
});
