import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';

import { MachinesView } from './MachinesView';

// Management → Machines (GUI_DESIGN §12, v15): operational monitoring
// plus permission-based configuration — lifecycle (active/retired)
// stays separate from the derived operational state, maintenance never
// moves quantity, and retirement is blocked while quantity is
// assigned. v15: the whole active row opens Edit Machine, maintenance
// toggles through a switch that only opens the existing dialogs,
// Retire lives in the Edit dialog's Danger Zone behind a typed
// confirmation plus a final summary (v17), and retired records offer
// Reactivate for the SAME physical machine — also behind a final
// summary (v17).

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

/** The Retired Machines table (renders below the active table). */
function retiredTable(): HTMLElement {
  const table = document.querySelectorAll('.mg-table')[1];
  expect(table).toBeDefined();
  return table as HTMLElement;
}

function maintenanceSwitch(name: string): HTMLElement {
  return within(activeRow(name)).getByRole('switch', {
    name: `Maintenance — ${name}`,
  });
}

/** Open Edit Machine through the whole-row click (v15). */
function openEdit(name: string): HTMLElement {
  fireEvent.click(activeRow(name));
  return screen.getByRole('dialog', { name: 'Edit Machine' });
}

test('active Machines list derived states with the time in state', () => {
  render(<MachinesView />);

  // Running derives from assigned quantity; the age derives from the
  // shared stateChangedAt timestamp.
  const lathe2 = activeRow('Lathe 2');
  expect(lathe2.querySelector('.mg-state')?.textContent).toMatch(
    /^Running · \d/,
  );
  // v16 mock data: Lathe 2 runs 0455-20-0118-03 (area-board.ts).
  expect(lathe2.textContent).toContain('0455-20-0118-03');

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

test('the replacement pair stays distinguishable: retired records keep their identity', () => {
  render(<MachinesView />);

  // The active `Lathe 1` is the replacement asset…
  expect(activeRow('Lathe 1').textContent).toContain('CD-0512');

  // …the retired predecessor keeps the SAME display name but its own
  // asset identity, retirement date and explanatory note.
  const retired = retiredTable();
  expect(retired.querySelectorAll('tbody tr')).toHaveLength(2);
  const retiredRow = within(retired)
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  expect(retiredRow.querySelector('.mgname')?.textContent).toBe('Lathe 1');
  expect(retiredRow.textContent).toContain('CD-0104');
  expect(retiredRow.textContent).toContain('Replaced by asset CD-0512');
  // Historical display with exactly one action per row: reactivation
  // of the same physical machine (v15) — no edit, no delete.
  expect(within(retiredRow).getAllByRole('button')).toHaveLength(1);
  expect(
    within(retiredRow).getByRole('button', { name: 'Reactivate…' }),
  ).toBeInTheDocument();

  // Second retired record (v15): Saw 2 has no asset tag — its barcode
  // is the typed-confirmation identifier for lifecycle actions.
  const saw2Row = within(retired)
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  expect(saw2Row.querySelector('.mgname')?.textContent).toBe('Saw 2');
  expect(saw2Row.querySelector('.mg-assetline')).toBeNull();
  expect(saw2Row.textContent).toContain(
    'Kept in storage — may return to service after overhaul.',
  );
});

test('the whole active row opens Edit Machine with the Area fixed', () => {
  render(<MachinesView />);

  const dialog = openEdit('Lathe 2');
  expect(within(dialog).getByLabelText('Display name')).toHaveValue('Lathe 2');
  expect(within(dialog).getByLabelText('Barcode value')).toHaveValue('L2');
  // A Machine belongs to exactly one Area — no Area select on edit,
  // only the read-only Area with its plain-language explanation (v17).
  expect(within(dialog).queryByRole('combobox')).toBeNull();
  expect(dialog.textContent).toContain(
    'stays fixed for its whole service life',
  );

  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the Maintenance switch mirrors the real state and only opens the dialogs', () => {
  render(<MachinesView />);

  // aria-checked reflects the real state only.
  expect(maintenanceSwitch('Lathe 2')).toHaveAttribute('aria-checked', 'false');
  expect(maintenanceSwitch('Lathe 4')).toHaveAttribute('aria-checked', 'true');

  // Toggling opens Start Maintenance — the Maintenance cell stops
  // propagation, so the row's Edit dialog never opens with it.
  fireEvent.click(maintenanceSwitch('Lathe 2'));
  expect(screen.queryByRole('dialog', { name: 'Edit Machine' })).toBeNull();
  const dialog = screen.getByRole('dialog', { name: 'Start maintenance' });

  // A cancelled dialog leaves the switch (the real state) untouched.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(maintenanceSwitch('Lathe 2')).toHaveAttribute('aria-checked', 'false');
});

test('starting maintenance keeps the assigned quantity in place', () => {
  render(<MachinesView />);

  fireEvent.click(maintenanceSwitch('Lathe 2'));
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
  expect(maintenanceSwitch('Lathe 2')).toHaveAttribute('aria-checked', 'true');
  // The assigned PN portions are untouched by the state change.
  expect(row.textContent).toContain('0455-20-0118-03');
});

test('clearing maintenance returns to Running with quantity, Idle without', () => {
  render(<MachinesView />);

  // Lathe 4 has no assigned quantity → clears to Idle.
  fireEvent.click(maintenanceSwitch('Lathe 4'));
  const dialog = screen.getByRole('dialog', { name: 'Clear maintenance' });
  expect(dialog.textContent).toContain('Idle');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Clear maintenance' }),
  );
  expect(activeRow('Lathe 4').querySelector('.mg-state')?.textContent).toMatch(
    /^Idle · /,
  );
  expect(maintenanceSwitch('Lathe 4')).toHaveAttribute('aria-checked', 'false');
});

test('retirement is blocked while quantity is assigned', () => {
  render(<MachinesView />);

  const edit = openEdit('Lathe 2');
  expect(edit.textContent).toContain(
    'Retirement is blocked while 4 pcs are still assigned.',
  );
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));

  const blocked = screen.getByRole('dialog', { name: 'Cannot retire Machine' });
  expect(blocked.textContent).toContain('4 pcs');
  expect(blocked.textContent).toContain('normal production workflow');
  // No confirming action exists — only Close.
  expect(within(blocked).queryByRole('button', { name: /Retire/ })).toBeNull();
  fireEvent.click(within(blocked).getByRole('button', { name: 'Close' }));

  // Back in the still-open Edit dialog; the Machine stays active.
  const editAgain = screen.getByRole('dialog', { name: 'Edit Machine' });
  fireEvent.click(
    within(editAgain).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(activeRow('Lathe 2')).toBeTruthy();
});

test('an idle Machine retires after typing its Asset Tag and a final summary — never deleted', () => {
  render(<MachinesView />);

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));

  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  expect(confirm.textContent).toContain(
    'It disappears from Machine assignment choices.',
  );
  expect(confirm.textContent).toContain('no longer accepts assignment scans');
  expect(confirm.textContent).toContain('nothing is deleted');
  expect(confirm.textContent).toContain('The record moves to Retired Machines');

  // Continue stays disabled until the Asset Tag is typed (trim +
  // case-insensitive deliberate acknowledgement).
  const continueButton = within(confirm).getByRole('button', {
    name: 'Continue',
  });
  const gate = within(confirm).getByLabelText(/to confirm$/);
  expect(gate).toHaveAttribute('placeholder', 'CD-0303');
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: 'CD-0304' } });
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: '  cd-0303 ' } });
  expect(continueButton).toBeEnabled();
  fireEvent.click(continueButton);

  // The typed confirmation leads to a final summary (v17) — nothing
  // has been retired yet.
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('Mill 3 — Horizontal Boring');
  expect(summary.textContent).toContain('CD-0303');
  expect(summary.textContent).toContain('nothing has changed yet');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );

  // Gone from the active table…
  expect(screen.queryByRole('dialog')).toBeNull();
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).not.toContain('Mill 3 — Horizontal Boring');
  // …and present under Retired Machines with its asset metadata.
  const retired = retiredTable();
  expect(retired.textContent).toContain('Mill 3 — Horizontal Boring');
  expect(retired.textContent).toContain('CD-0303');
});

test('the retire edits decision is recorded, not applied — cancelling later keeps the edits', () => {
  render(<MachinesView />);

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Pending disposal review' },
  });
  expect(edit.textContent).toContain('● Unsaved changes');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));

  // Discard is a DECISION for the retirement, not an immediate reset.
  const unsaved = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(unsaved).getByRole('button', { name: 'Save changes' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(unsaved).getByRole('button', { name: 'Discard changes' }),
  );

  // Cancelling the typed confirmation returns to the form with the
  // edits still in place.
  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  const editAgain = screen.getByRole('dialog', { name: 'Edit Machine' });
  expect(within(editAgain).getByLabelText('Notes (optional)')).toHaveValue(
    'Pending disposal review',
  );
  expect(editAgain.textContent).toContain('● Unsaved changes');
});

test('a recorded Save decision applies the edits only when the retirement completes', () => {
  render(<MachinesView />);

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Sold for scrap' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));
  const unsaved = screen.getByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(
    within(unsaved).getByRole('button', { name: 'Save changes' }),
  );

  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  fireEvent.change(within(confirm).getByLabelText(/to confirm$/), {
    target: { value: 'CD-0303' },
  });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Continue' }));

  // The summary names the recorded decision before anything happens.
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('Saved with the retirement');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );

  // The edit was applied together with the retirement.
  expect(retiredTable().textContent).toContain('Sold for scrap');
});

test('reactivation blocks on a name collision until a rename, then returns the Machine as Idle', () => {
  render(<MachinesView />);

  const lathe1Row = within(retiredTable())
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  fireEvent.click(
    within(lathe1Row).getByRole('button', { name: 'Reactivate…' }),
  );

  const dialog = screen.getByRole('dialog', { name: 'Reactivate Machine' });
  expect(dialog.textContent).toContain('returns to service on the SAME record');
  // The reused floor-position name collides with the active replacement
  // MC-512 `Lathe 1` in the same Area.
  expect(dialog.textContent).toContain(
    'An active Machine named “Lathe 1” already exists in Lathe',
  );

  // A required reason alone is not enough while the collision stands.
  fireEvent.change(within(dialog).getByLabelText('Reason (required)'), {
    target: { value: 'Returned from overhaul' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(dialog.textContent).toContain('rename this Machine to continue');
  expect(
    screen.getByRole('dialog', { name: 'Reactivate Machine' }),
  ).toBeInTheDocument();

  // Renaming inside the dialog resolves the collision…
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 1B' },
  });
  expect(dialog.textContent).not.toContain(
    'already exists in Lathe — rename one of them',
  );
  // …and the same-physical-machine confirmation stays required.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(dialog.textContent).toContain(
    'Confirm that this is the same physical machine.',
  );
  fireEvent.click(within(dialog).getByRole('checkbox'));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

  // The form leads to a final summary (v17) — reactivation happens
  // only on its confirmation.
  const summary = screen.getByRole('dialog', { name: 'Confirm reactivation' });
  expect(summary.textContent).toContain('Lathe 1B');
  expect(summary.textContent).toContain('Returned from overhaul');
  expect(summary.textContent).toContain('Idle');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Reactivate Machine' }),
  );

  // The Machine returns as Idle (running stays derived).
  expect(screen.queryByRole('dialog')).toBeNull();
  const row = activeRow('Lathe 1B');
  expect(row.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);

  // The lifecycle audit keeps both events, append-only.
  fireEvent.click(row);
  const edit = screen.getByRole('dialog', { name: 'Edit Machine' });
  const events = edit.querySelectorAll('.mg-lifeevent');
  expect(events).toHaveLength(2);
  expect(events[0].textContent).toContain('RETIRED');
  expect(events[1].textContent).toContain('REACTIVATED');
  expect(events[1].textContent).toContain('M. Chen (Production Manager)');
  expect(events[1].textContent).toContain('Returned from overhaul');
});

test('a Machine without an Asset Tag confirms retirement with its barcode', () => {
  render(<MachinesView />);

  // Reactivate Saw 2 first (no identity conflicts, no name collision).
  const saw2Row = within(retiredTable())
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  fireEvent.click(within(saw2Row).getByRole('button', { name: 'Reactivate…' }));
  const dialog = screen.getByRole('dialog', { name: 'Reactivate Machine' });
  expect(dialog.querySelector('.mg-blockers')).toBeNull();
  fireEvent.change(within(dialog).getByLabelText('Reason (required)'), {
    target: { value: 'Back from gearbox overhaul' },
  });
  fireEvent.click(within(dialog).getByRole('checkbox'));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  const reactSummary = screen.getByRole('dialog', {
    name: 'Confirm reactivation',
  });
  fireEvent.click(
    within(reactSummary).getByRole('button', { name: 'Reactivate Machine' }),
  );
  expect(activeRow('Saw 2').querySelector('.mg-state')?.textContent).toMatch(
    /^Idle · /,
  );

  // Retire it again: without an asset tag the typed confirmation falls
  // back to the Machine barcode (always present).
  const edit = openEdit('Saw 2');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));
  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  expect(confirm.textContent).toContain('(Machine barcode) to confirm');
  const gate = within(confirm).getByLabelText(/to confirm$/);
  expect(gate).toHaveAttribute('placeholder', 'S2');
  const continueButton = within(confirm).getByRole('button', {
    name: 'Continue',
  });
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: 's2' } });
  expect(continueButton).toBeEnabled();
  fireEvent.click(continueButton);
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('S2');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );

  expect(retiredTable().textContent).toContain('Saw 2');
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
