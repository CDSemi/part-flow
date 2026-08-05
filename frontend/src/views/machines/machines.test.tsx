import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';

import { MachinesView } from './MachinesView';

// Management → Machines (GUI_DESIGN §12): operational monitoring plus
// permission-based configuration — lifecycle (active/retired) stays
// separate from the derived operational state, maintenance never moves
// quantity, and retirement is blocked while quantity is assigned.

beforeEach(() => {
  window.history.replaceState({}, '', '/management/machines');
});

afterEach(cleanup);

/** The active-Machines table row whose first cell names the Machine. */
function activeRow(name: string): HTMLElement {
  const table = document.querySelectorAll('.mg-table')[0];
  const row = Array.from(table.querySelectorAll('tbody tr')).find(
    (tr) => tr.querySelector('.mgname')?.textContent === name,
  );
  expect(row).toBeDefined();
  return row as HTMLElement;
}

test('active Machines list derived states with the time in state', () => {
  render(<MachinesView />);

  // Running derives from assigned quantity; the age derives from the
  // shared stateChangedAt timestamp.
  const lathe2 = activeRow('Lathe 2');
  expect(lathe2.querySelector('.mg-state')?.textContent).toMatch(
    /^Running · \d/,
  );
  expect(lathe2.textContent).toContain('2027-60-8114-00');

  // No assignment → Idle (derived, never chosen).
  expect(
    activeRow('Mill 3 — Horizontal Boring').querySelector('.mg-state')
      ?.textContent,
  ).toMatch(/^Idle · /);

  // Explicit maintenance override with its note and expected return.
  const lathe4 = activeRow('Lathe 4');
  expect(lathe4.querySelector('.mg-state')?.textContent).toMatch(
    /^Maintenance · /,
  );
  expect(lathe4.textContent).toContain('Spindle bearing replacement');
  expect(lathe4.textContent).toContain('Expected back 2026-08-06');
});

test('the replacement pair stays distinguishable: retired record keeps its identity', () => {
  render(<MachinesView />);

  // The active `Lathe 1` is the replacement asset…
  expect(activeRow('Lathe 1').textContent).toContain('CD-0512');

  // …the retired predecessor keeps the SAME display name but its own
  // asset identity, retirement date and explanatory note.
  const retiredTable = document.querySelectorAll('.mg-table')[1];
  const retiredRow = within(retiredTable as HTMLElement)
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  expect(retiredRow.querySelector('.mgname')?.textContent).toBe('Lathe 1');
  expect(retiredRow.textContent).toContain('CD-0104');
  expect(retiredRow.textContent).toContain('Replaced by asset CD-0512');
  // Historical display only — no action buttons on retired records.
  expect(retiredRow.querySelectorAll('button').length).toBe(0);
});

test('starting maintenance keeps the assigned quantity in place', () => {
  render(<MachinesView />);

  fireEvent.click(
    within(activeRow('Lathe 2')).getByRole('button', {
      name: 'Start maintenance',
    }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Start maintenance' });
  // The dialog states explicitly that nothing moves.
  expect(dialog.textContent).toContain('4 pcs stay assigned');
  fireEvent.change(within(dialog).getByLabelText(/Reason \/ note/), {
    target: { value: 'Coolant leak' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Start maintenance' }),
  );

  const row = activeRow('Lathe 2');
  expect(row.querySelector('.mg-state')?.textContent).toMatch(
    /^Maintenance · /,
  );
  expect(row.textContent).toContain('Coolant leak');
  // The assigned PN portions are untouched by the state change.
  expect(row.textContent).toContain('2027-60-8114-00');
});

test('clearing maintenance returns to Running with quantity, Idle without', () => {
  render(<MachinesView />);

  // Lathe 4 has no assigned quantity → clears to Idle.
  fireEvent.click(
    within(activeRow('Lathe 4')).getByRole('button', {
      name: 'Clear maintenance',
    }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Clear maintenance' });
  expect(dialog.textContent).toContain('Idle');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Clear maintenance' }),
  );
  expect(activeRow('Lathe 4').querySelector('.mg-state')?.textContent).toMatch(
    /^Idle · /,
  );
});

test('retirement is blocked while quantity is assigned', () => {
  render(<MachinesView />);

  fireEvent.click(
    within(activeRow('Lathe 2')).getByRole('button', { name: 'Retire…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Cannot retire Machine' });
  expect(dialog.textContent).toContain('4 pcs');
  expect(dialog.textContent).toContain('normal production workflow');
  // No confirming action exists — only Close.
  expect(within(dialog).queryByRole('button', { name: /Retire/ })).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));

  // Still active.
  expect(activeRow('Lathe 2')).toBeTruthy();
});

test('an idle Machine retires into the Retired section — never deleted', () => {
  render(<MachinesView />);

  fireEvent.click(
    within(activeRow('Mill 3 — Horizontal Boring')).getByRole('button', {
      name: 'Retire…',
    }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retire Machine' });
  expect(dialog.textContent).toContain('stops accepting new work');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Retire Machine' }),
  );

  // Gone from the active table…
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).not.toContain('Mill 3 — Horizontal Boring');
  // …and present under Retired Machines with its asset metadata.
  const retiredTable = document.querySelectorAll('.mg-table')[1];
  expect(retiredTable.textContent).toContain('Mill 3 — Horizontal Boring');
  expect(retiredTable.textContent).toContain('CD-0303');
});

test('a new Machine requires a unique barcode', () => {
  render(<MachinesView />);

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 5' },
  });
  fireEvent.change(within(dialog).getByLabelText('Barcode value'), {
    target: { value: 'L2' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Machine' }));
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'already used by another Machine',
  );

  fireEvent.change(within(dialog).getByLabelText('Barcode value'), {
    target: { value: 'L5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Machine' }));

  // The new Machine starts Idle (no assignment yet).
  const row = activeRow('Lathe 5');
  expect(row.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);
});
