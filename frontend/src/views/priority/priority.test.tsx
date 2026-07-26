import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityProvider } from '../../app/connectivity-provider';
import { PriorityView } from './PriorityView';

// Priority regressions: every operation that reorders existing Hot
// entries (drag and drop, Move Up, Move Down, Undo, Redo) requires
// confirmation before applying; cancelling leaves the list, the undo
// history and the redo history unchanged; the visible list is never
// renumbered before confirmation.

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

test('Move Down asks for confirmation; cancel changes nothing', async () => {
  await renderPriority();
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );

  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Move Down');
  expect(dialog).toHaveTextContent('2027-60-8114-00');
  expect(dialog).toHaveTextContent('#1');
  expect(dialog).toHaveTextContent('#2');
  // The visible list is not renumbered before confirmation.
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — nothing changes' }),
  );
  expect(listedPns()).toEqual(INITIAL);
  // Histories unchanged: nothing to undo or redo.
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeDisabled();
});

test('Move Down applies only after confirmation', async () => {
  await renderPriority();

  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Apply new ranking' }));

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
  fireEvent.click(screen.getByRole('button', { name: 'Apply new ranking' }));

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

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — nothing changes' }),
  );
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeDisabled();

  // The same drop applies after confirmation.
  const itemsAgain = document.querySelectorAll('.pr-item');
  fireEvent.dragStart(itemsAgain[0]);
  fireEvent.drop(itemsAgain[2]);
  fireEvent.click(screen.getByRole('button', { name: 'Apply new ranking' }));
  expect(listedPns()).toEqual(['142-260', '309-127', '2027-60-8114-00']);
});

test('Undo and Redo also require confirmation; cancel preserves both histories', async () => {
  await renderPriority();

  // Create one confirmed change so Undo becomes available.
  fireEvent.click(
    screen.getByRole('button', { name: 'Move 2027-60-8114-00 down' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Apply new ranking' }));
  const reordered = ['142-260', '2027-60-8114-00', '309-127'];
  expect(listedPns()).toEqual(reordered);

  // Undo: cancel first — list, undo history and redo history unchanged.
  fireEvent.click(screen.getByRole('button', { name: '⟲ Undo' }));
  let dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Undo');
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — nothing changes' }),
  );
  expect(listedPns()).toEqual(reordered);
  expect(screen.getByRole('button', { name: '⟲ Undo' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeDisabled();

  // Undo: confirm — the previous ranking is restored.
  fireEvent.click(screen.getByRole('button', { name: '⟲ Undo' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply new ranking' }));
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeEnabled();

  // Redo: cancel keeps everything; confirm re-applies.
  fireEvent.click(screen.getByRole('button', { name: '⟳ Redo' }));
  dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Redo');
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — nothing changes' }),
  );
  expect(listedPns()).toEqual(INITIAL);
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeEnabled();

  fireEvent.click(screen.getByRole('button', { name: '⟳ Redo' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply new ranking' }));
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
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — nothing changes' }),
  );
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove 309-127 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  expect(listedPns()).toEqual(['2027-60-8114-00', '142-260']);
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
