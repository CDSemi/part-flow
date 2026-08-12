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
  // Historical display with exactly two entry points per row: the
  // read-only details (whole row / name-cell button) and reactivation
  // of the same physical machine — no edit, no delete.
  expect(within(retiredRow).getAllByRole('button')).toHaveLength(2);
  expect(
    within(retiredRow).getByRole('button', {
      name: 'Machine details — Lathe 1',
    }),
  ).toBeInTheDocument();
  expect(
    within(retiredRow).getByRole('button', { name: 'Reactivate' }),
  ).toBeInTheDocument();

  // Second retired record: retired Machines keep their Asset Tag
  // forever — the tag is never reused by a later Machine.
  const saw2Row = within(retired)
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  expect(saw2Row.querySelector('.mgname')?.textContent).toBe('Saw 2');
  expect(saw2Row.querySelector('.mg-assetline')?.textContent).toContain(
    'CD-0202',
  );
  expect(saw2Row.textContent).toContain(
    'Kept in storage — may return to service after overhaul.',
  );
});

test('the whole active row opens Edit Machine with the Area fixed', () => {
  render(<MachinesView />);

  const dialog = openEdit('Lathe 2');
  expect(within(dialog).getByLabelText('Display name')).toHaveValue('Lathe 2');
  // The identity header leads the dialog: Asset Tag and its derived
  // barcode as read-only values — there is no input for either — plus
  // the barcode-label entry.
  expect(within(dialog).queryByLabelText('Barcode value')).toBeNull();
  expect(within(dialog).queryByLabelText(/Asset tag/)).toBeNull();
  const identity = dialog.querySelector('.mg-idhead') as HTMLElement;
  expect(identity.textContent).toContain('CD-0105');
  expect(identity.textContent).toContain('PF:MACHINE:CD-0105');
  expect(
    within(identity).getByRole('button', { name: 'Barcode label…' }),
  ).toBeInTheDocument();
  // A Machine belongs to exactly one Area — no Area select on edit;
  // the fixed Area lives in the identity header with its plain-language
  // explanation.
  expect(within(dialog).queryByRole('combobox')).toBeNull();
  expect(identity.textContent).toContain(
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

  // The summary's action asks one last explicit question — the
  // retirement is recorded in the lifecycle and cannot be undone.
  const ask = screen.getByRole('dialog', { name: 'Retire this Machine?' });
  expect(ask.textContent).toContain('cannot be undone');
  fireEvent.click(within(ask).getByRole('button', { name: 'Retire Machine' }));

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
  const ask = screen.getByRole('dialog', { name: 'Retire this Machine?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Retire Machine' }));

  // The edit was applied together with the retirement.
  expect(retiredTable().textContent).toContain('Sold for scrap');
});

test('reactivation blocks on a name collision until a rename, then returns the Machine as Idle', () => {
  render(<MachinesView />);

  const lathe1Row = within(retiredTable())
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  fireEvent.click(
    within(lathe1Row).getByRole('button', { name: 'Reactivate' }),
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

  // One last explicit question — reactivation is recorded in the
  // lifecycle and cannot be undone.
  const ask = screen.getByRole('dialog', { name: 'Reactivate this Machine?' });
  expect(ask.textContent).toContain('cannot be undone');
  fireEvent.click(
    within(ask).getByRole('button', { name: 'Reactivate Machine' }),
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

test('a reactivated Machine keeps its Asset Tag and confirms retirement with it', () => {
  render(<MachinesView />);

  // Reactivate Saw 2 first (no identity conflicts, no name collision).
  const saw2Row = within(retiredTable())
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  fireEvent.click(within(saw2Row).getByRole('button', { name: 'Reactivate' }));
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
  // The summary recaps the untouched identity: Asset Tag and the
  // barcode derived from it.
  expect(reactSummary.textContent).toContain('CD-0202');
  expect(reactSummary.textContent).toContain('PF:MACHINE:CD-0202');
  fireEvent.click(
    within(reactSummary).getByRole('button', { name: 'Reactivate Machine' }),
  );
  const reactAsk = screen.getByRole('dialog', {
    name: 'Reactivate this Machine?',
  });
  fireEvent.click(
    within(reactAsk).getByRole('button', { name: 'Reactivate Machine' }),
  );
  expect(activeRow('Saw 2').querySelector('.mg-state')?.textContent).toMatch(
    /^Idle · /,
  );

  // Retire it again: the typed confirmation is always the Asset Tag —
  // never the reusable display name.
  const edit = openEdit('Saw 2');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));
  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  expect(confirm.textContent).toContain('(Asset Tag) to confirm');
  const gate = within(confirm).getByLabelText(/to confirm$/);
  expect(gate).toHaveAttribute('placeholder', 'CD-0202');
  const continueButton = within(confirm).getByRole('button', {
    name: 'Continue',
  });
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: 'cd-0202' } });
  expect(continueButton).toBeEnabled();
  fireEvent.click(continueButton);
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('CD-0202');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );
  const retireAsk = screen.getByRole('dialog', {
    name: 'Retire this Machine?',
  });
  fireEvent.click(
    within(retireAsk).getByRole('button', { name: 'Retire Machine' }),
  );

  expect(retiredTable().textContent).toContain('Saw 2');
});

test('a new Machine receives the next Asset Tag automatically and is added only after summary + confirmation', () => {
  render(<MachinesView />);

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  // No manual identity entry exists: the Asset Tag is assigned from
  // the configured format (highest existing sequence is CD-0512) and
  // the barcode derives from it. The identity header leads the dialog.
  expect(within(dialog).queryByLabelText('Barcode value')).toBeNull();
  expect(within(dialog).queryByLabelText(/Asset tag/)).toBeNull();
  const identity = dialog.querySelector('.mg-idhead') as HTMLElement;
  expect(identity.textContent).toContain('CD-0513');
  expect(identity.textContent).toContain('PF:MACHINE:CD-0513');
  // A new Machine has no barcode label yet — the label entry is for
  // existing Machines only.
  expect(
    within(dialog).queryByRole('button', { name: 'Barcode label…' }),
  ).toBeNull();
  // The Area select shares the form with the Display name.
  expect(within(dialog).getByRole('combobox')).toBeInTheDocument();

  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

  // Continue leads to a summary — nothing has been added yet.
  const summary = screen.getByRole('dialog', { name: 'Confirm new Machine' });
  expect(summary.textContent).toContain('nothing has been added yet');
  expect(summary.textContent).toContain('Lathe 5');
  expect(summary.textContent).toContain('CD-0513');
  expect(summary.textContent).toContain('PF:MACHINE:CD-0513');
  fireEvent.click(within(summary).getByRole('button', { name: 'Add Machine' }));

  // The summary's action asks one last explicit question — a Machine
  // record can never be deleted, only retired.
  const ask = screen.getByRole('dialog', { name: 'Add this Machine?' });
  expect(ask.textContent).toContain('can only be retired later');
  fireEvent.click(within(ask).getByRole('button', { name: 'Add Machine' }));

  // The new Machine starts Idle (no assignment yet) and carries the
  // assigned Asset Tag.
  const row = activeRow('Lathe 5');
  expect(row.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);
  expect(row.querySelector('.mg-assetline')?.textContent).toContain('CD-0513');
});

test('cancelling the add confirmation returns to the summary, then the form — nothing added', () => {
  render(<MachinesView />);

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  const summary = screen.getByRole('dialog', { name: 'Confirm new Machine' });
  fireEvent.click(within(summary).getByRole('button', { name: 'Add Machine' }));
  const ask = screen.getByRole('dialog', { name: 'Add this Machine?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Cancel (Esc)' }));

  // Back on the summary; Back returns to the form with its input kept.
  const summaryAgain = screen.getByRole('dialog', {
    name: 'Confirm new Machine',
  });
  fireEvent.click(within(summaryAgain).getByRole('button', { name: 'Back' }));
  expect(
    within(screen.getByRole('dialog', { name: 'New Machine' })).getByLabelText(
      'Display name',
    ),
  ).toHaveValue('Lathe 5');
  // Nothing was added.
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).not.toContain('Lathe 5');
});

test('a new Machine cannot reuse an active display name of the same Area', () => {
  render(<MachinesView />);

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  // The default Area is Lathe, where an active `Lathe 2` exists.
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 2' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'already exists in Lathe',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Confirm new Machine' }),
  ).toBeNull();
});

/** Display names of the active table, in row order. */
function activeNames(): (string | null)[] {
  const table = document.querySelectorAll('.mg-table')[0];
  return Array.from(table.querySelectorAll('tbody .mgname')).map(
    (cell) => cell.textContent,
  );
}

test('column headers sort the active table and cycle ascending → descending → unsorted', () => {
  render(<MachinesView />);

  const original = [
    'Saw 1',
    'Lathe 1',
    'Lathe 2',
    'Lathe 3',
    'Lathe 4',
    'Mill 1',
    'Mill 2',
    'Mill 3 — Horizontal Boring',
  ];
  expect(activeNames()).toEqual(original);

  const byMachine = screen.getByRole('button', { name: 'Sort by Machine' });
  // Unsorted headers carry the neutral both-ways arrow, no emphasis.
  expect(byMachine).not.toHaveClass('on');
  expect(byMachine.textContent).toContain('↕');

  fireEvent.click(byMachine);
  expect(activeNames()).toEqual([
    'Lathe 1',
    'Lathe 2',
    'Lathe 3',
    'Lathe 4',
    'Mill 1',
    'Mill 2',
    'Mill 3 — Horizontal Boring',
    'Saw 1',
  ]);
  expect(byMachine).toHaveClass('on');
  expect(byMachine.textContent).toContain('↑');
  expect(byMachine.closest('th')).toHaveAttribute('aria-sort', 'ascending');

  fireEvent.click(byMachine);
  expect(activeNames()).toEqual([
    'Saw 1',
    'Mill 3 — Horizontal Boring',
    'Mill 2',
    'Mill 1',
    'Lathe 4',
    'Lathe 3',
    'Lathe 2',
    'Lathe 1',
  ]);
  expect(byMachine.textContent).toContain('↓');
  expect(byMachine.closest('th')).toHaveAttribute('aria-sort', 'descending');

  // Third click returns to the unsorted registry order.
  fireEvent.click(byMachine);
  expect(activeNames()).toEqual(original);
  expect(byMachine).not.toHaveClass('on');
  expect(byMachine.closest('th')).not.toHaveAttribute('aria-sort');
});

test('Assigned now sorts by quantity and State by derived state — ties stay in name order', () => {
  render(<MachinesView />);

  // Assigned quantities (mock): Saw 1 = 4, Lathe 2 = 4, Lathe 3 = 3,
  // Mill 1 = 3, Mill 2 = 2, others 0.
  fireEvent.click(screen.getByRole('button', { name: 'Sort by Assigned now' }));
  expect(activeNames()).toEqual([
    'Lathe 1',
    'Lathe 4',
    'Mill 3 — Horizontal Boring',
    'Mill 2',
    'Lathe 3',
    'Mill 1',
    'Lathe 2',
    'Saw 1',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Sort by Assigned now' }));
  expect(activeNames()).toEqual([
    'Lathe 2',
    'Saw 1',
    'Lathe 3',
    'Mill 1',
    'Mill 2',
    'Lathe 1',
    'Lathe 4',
    'Mill 3 — Horizontal Boring',
  ]);

  // Switching to another column starts a fresh ascending sort:
  // Running first, then Idle, Maintenance last (ties by name).
  fireEvent.click(screen.getByRole('button', { name: 'Sort by State' }));
  expect(activeNames()).toEqual([
    'Lathe 2',
    'Lathe 3',
    'Mill 1',
    'Mill 2',
    'Saw 1',
    'Lathe 1',
    'Mill 3 — Horizontal Boring',
    'Lathe 4',
  ]);
});

test('the Retired Machines table sorts independently through its own headers', () => {
  render(<MachinesView />);

  /** Display names of the retired table, in row order. */
  const retiredNames = () =>
    Array.from(retiredTable().querySelectorAll('tbody .mgname')).map(
      (cell) => cell.textContent,
    );
  // Registry order: the retired Lathe 1 (2026-02-14), then Saw 2
  // (2025-11-03).
  expect(retiredNames()).toEqual(['Lathe 1', 'Saw 2']);

  // Ascending by retirement date puts the older retirement first.
  const byRetired = screen.getByRole('button', {
    name: 'Sort Retired Machines by Retired',
  });
  fireEvent.click(byRetired);
  expect(retiredNames()).toEqual(['Saw 2', 'Lathe 1']);
  expect(byRetired).toHaveClass('on');
  expect(byRetired.closest('th')).toHaveAttribute('aria-sort', 'ascending');

  fireEvent.click(byRetired);
  expect(retiredNames()).toEqual(['Lathe 1', 'Saw 2']);
  expect(byRetired.closest('th')).toHaveAttribute('aria-sort', 'descending');

  // Third click returns to the registry order; the active table's own
  // sort state is untouched throughout.
  fireEvent.click(byRetired);
  expect(retiredNames()).toEqual(['Lathe 1', 'Saw 2']);
  expect(byRetired).not.toHaveClass('on');
  expect(
    screen.getByRole('button', { name: 'Sort by Machine' }),
  ).not.toHaveClass('on');
});

test('the Retired Machines columns order Machine, Asset, Retired, Notes, Reactivate', () => {
  render(<MachinesView />);

  const headers = Array.from(
    retiredTable().querySelectorAll('thead th'),
    (th) => th.textContent?.replace(/[↕↑↓]/g, '').trim(),
  );
  expect(headers).toEqual([
    'Machine',
    'Asset',
    'Retired',
    'Notes',
    'Reactivate',
  ]);
});

test('a retired row opens the read-only Retired Machine Details dialog with the lifecycle', () => {
  render(<MachinesView />);

  const lathe1Row = within(retiredTable())
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  fireEvent.click(lathe1Row);

  const dialog = screen.getByRole('dialog', {
    name: 'Retired Machine Details',
  });
  // Identity and the untouched asset metadata of the record.
  expect(dialog.textContent).toContain('Lathe 1');
  expect(dialog.textContent).toContain('Retired on 2026-02-14');
  expect(dialog.textContent).toContain('CD-0104');
  expect(dialog.textContent).toContain('PF:MACHINE:CD-0104');
  expect(dialog.textContent).toContain('Mazak');
  expect(dialog.textContent).toContain('QT-10');
  expect(dialog.textContent).toContain('Q10-61208');
  expect(dialog.textContent).toContain(
    'display name reused for the floor position',
  );
  // The lifecycle timeline presents the append-only audit events.
  const events = dialog.querySelectorAll('.mg-tlevent');
  expect(events).toHaveLength(1);
  expect(events[0].textContent).toContain('RETIRED');
  expect(events[0].textContent).toContain('2026-02-14');
  expect(events[0].textContent).toContain('M. Chen (Production Manager)');
  expect(events[0].textContent).toContain('Replaced by asset CD-0512');
  // Read-only: no inputs — Close and the Reactivate entry only.
  expect(dialog.querySelector('input')).toBeNull();

  // Close changes nothing.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(retiredTable().textContent).toContain('Retired on 2026-02-14');
});

test('the Reactivate cell never opens the details; the details dialog leads to Reactivate', () => {
  render(<MachinesView />);

  const saw2Row = within(retiredTable())
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  // The action cell is the row's one interactive island — it stops
  // propagation, so Reactivate never also opens the details dialog.
  fireEvent.click(within(saw2Row).getByRole('button', { name: 'Reactivate' }));
  expect(
    screen.queryByRole('dialog', { name: 'Retired Machine Details' }),
  ).toBeNull();
  const reactivate = screen.getByRole('dialog', { name: 'Reactivate Machine' });
  fireEvent.click(
    within(reactivate).getByRole('button', { name: 'Cancel (Esc)' }),
  );

  // The details dialog offers the same staged Reactivate workflow.
  fireEvent.click(saw2Row);
  const details = screen.getByRole('dialog', {
    name: 'Retired Machine Details',
  });
  fireEvent.click(within(details).getByRole('button', { name: 'Reactivate' }));
  expect(
    screen.getByRole('dialog', { name: 'Reactivate Machine' }),
  ).toBeInTheDocument();
});

test('the maintenance note and expected return date are editable from Edit Machine', () => {
  render(<MachinesView />);

  // A Machine that is NOT under maintenance shows no maintenance
  // fields in the Edit dialog.
  const lathe2 = openEdit('Lathe 2');
  expect(within(lathe2).queryByLabelText(/Reason \/ note/)).toBeNull();
  fireEvent.click(within(lathe2).getByRole('button', { name: 'Cancel (Esc)' }));

  // Lathe 4 is under maintenance — the context is editable in place.
  const edit = openEdit('Lathe 4');
  expect(within(edit).getByLabelText(/Reason \/ note/)).toHaveValue(
    'Spindle bearing replacement',
  );
  expect(within(edit).getByLabelText(/Expected return date/)).toHaveValue(
    '2026-08-06',
  );
  fireEvent.change(within(edit).getByLabelText(/Reason \/ note/), {
    target: { value: 'Spindle rebuilt — waiting on parts' },
  });
  fireEvent.change(within(edit).getByLabelText(/Expected return date/), {
    target: { value: '2026-08-20' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));

  // The row shows the updated context; the state itself is untouched.
  const row = activeRow('Lathe 4');
  expect(row.querySelector('.mg-state')?.textContent).toMatch(
    /^Maintenance · /,
  );
  expect(row.textContent).toContain('Spindle rebuilt — waiting on parts');
  expect(row.textContent).toContain('Expected back 2026-08-20');
});

test('the barcode label dialog renders the scannable Asset Tag barcode', () => {
  render(<MachinesView />);

  const dialog = openEdit('Lathe 2');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Barcode label…' }),
  );
  const label = screen.getByRole('dialog', { name: 'Machine barcode label' });
  expect(label.textContent).toContain('Lathe 2');
  expect(label.textContent).toContain('CD-0105');
  expect(label.textContent).toContain('PF:MACHINE:CD-0105');
  // The Code 128 rendering is a real SVG barcode of the scanned value.
  const svg = label.querySelector('svg.lbarcode') as SVGElement;
  expect(svg).not.toBeNull();
  expect(svg.getAttribute('aria-label')).toBe('Barcode PF:MACHINE:CD-0105');
  expect(svg.querySelectorAll('rect').length).toBeGreaterThan(20);
  expect(
    within(label).getByRole('button', { name: 'Print Label' }),
  ).toBeInTheDocument();

  // Cancel returns to the Edit dialog.
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.getByRole('dialog', { name: 'Edit Machine' }),
  ).toBeInTheDocument();
});
