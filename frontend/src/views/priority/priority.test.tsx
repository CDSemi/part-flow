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

test('confirmation compares current and proposed ranks and highlights the moved item', async () => {
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

  // The directly moved item is distinguishable from indirect shifts.
  const movedRow = dialog.querySelector('.pr-compare tr.moved');
  expect(movedRow).not.toBeNull();
  expect(movedRow).toHaveTextContent('2027-60-8114-00');
  // Old and new ranks are both visible, with a downward direction.
  expect(movedRow).toHaveTextContent('#1');
  expect(movedRow).toHaveTextContent('#3');
  expect(movedRow).toHaveTextContent('↓');

  const shiftedRows = dialog.querySelectorAll('.pr-compare tr.shifted');
  expect(shiftedRows).toHaveLength(2);
  for (const row of Array.from(shiftedRows)) {
    expect(row).toHaveTextContent('↑');
  }
  expect(shiftedRows[0]).toHaveTextContent('142-260');
  expect(shiftedRows[1]).toHaveTextContent('309-127');

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
  expect(dialog.querySelector('.pr-compare tr.moved')).toBeNull();
  expect(dialog.querySelectorAll('.pr-compare tr.shifted')).toHaveLength(2);
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
  const row = dialog.querySelector('.pr-compare tbody tr');
  expect(row).toHaveTextContent('309-127');
  expect(row).toHaveTextContent('—'); // no current rank while removed
  expect(row).toHaveTextContent('#3');

  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeEnabled();
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
