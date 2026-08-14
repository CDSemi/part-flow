import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../../app/connectivity-context';
import { PlannedRoutesView } from './PlannedRoutesView';

// Management → Planned Routes (GUI_DESIGN §13, v15): reusable route
// definitions with the RouteTemplate semantics — edits affect future
// assignments only, used routes archive instead of deleting, archived
// routes stay visible but are no normal choice for new assignments.
// v15: active and archived routes split into separate tables, the
// whole active row opens the edit dialog, Duplicate/Archive…/Delete…
// live inside that dialog behind unsaved-edit gates, archiving needs
// the typed route name, and step Operation / Preferred Machine are
// selects scoped to the step's Area (Machines referenced by stable id).

beforeEach(() => {
  window.history.replaceState({}, '', '/management/planned-routes');
});

afterEach(cleanup);

/** Render Planned Routes with a fixed connectivity status —
 * deterministic, no fetch/timer polling. Defaults to `connected` so
 * the existing behavioral tests exercise a fully-enabled view;
 * offline-specific tests pass `'unavailable'` explicitly. */
function renderPlannedRoutes(
  status: 'connected' | 'unavailable' = 'connected',
) {
  return render(
    <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
      <PlannedRoutesView />
    </ConnectivityContext.Provider>,
  );
}

function routeRow(name: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('.rt-table tbody tr')).find(
    (tr) => tr.querySelector('.rtname')?.textContent === name,
  );
  expect(row).toBeDefined();
  return row as HTMLElement;
}

/** Open the edit dialog through the active row's accessible entry. */
function openEdit(name: string): HTMLElement {
  fireEvent.click(
    within(routeRow(name)).getByRole('button', { name: `Edit ${name}` }),
  );
  return screen.getByRole('dialog', { name: 'Edit Planned Route' });
}

test('active and archived routes split into separate tables without an Actions column', () => {
  renderPlannedRoutes();

  const bracket = routeRow('Bracket std v3');
  // Whole-row activation with the name-cell button as the keyboard and
  // screen-reader entry point.
  expect(bracket.className).toContain('selrow');
  expect(
    within(bracket).getByRole('button', { name: 'Edit Bracket std v3' }),
  ).toBeInTheDocument();
  // Area-colored step chips carry the step sequence.
  expect(
    Array.from(
      bracket.querySelectorAll('.rt-steps .rt-stepchip'),
      (el) => el.textContent,
    ),
  ).toEqual(['Material', 'Cut', 'Lathe', 'Deburr', 'Stockroom']);
  expect(bracket.querySelector('.rt-status')?.textContent).toBe('Active');
  // No Actions column — row actions moved into the edit dialog (v15).
  const activeTable = document.querySelectorAll('.rt-table')[0];
  expect(
    Array.from(
      activeTable.querySelectorAll('thead th'),
      (th) => th.textContent,
    ),
  ).toEqual(['Planned Route', 'Steps', 'Status', 'Used by']);

  // Archived routes live in their own section; rows are not clickable
  // and offer only duplication into a fresh active route.
  const legacy = routeRow('Legacy plating route');
  expect(legacy.closest('.rt-archived')).not.toBeNull();
  expect(legacy.className).toContain('archived');
  expect(legacy.className).not.toContain('selrow');
  expect(legacy.querySelector('.rt-status')?.textContent).toBe('Archived');
  expect(within(legacy).queryByRole('button', { name: /^Edit / })).toBeNull();
  expect(
    within(legacy).getByRole('button', { name: 'Duplicate' }),
  ).toBeInTheDocument();
});

test('usage inspection lists the Quantity Flows released with the route', () => {
  renderPlannedRoutes();

  // The usage cell is an interactive island — it opens the usage
  // dialog, never the row's edit dialog.
  fireEvent.click(
    within(routeRow('Bracket std v3')).getByRole('button', {
      name: /2 Quantity Flows/,
    }),
  );
  expect(
    screen.queryByRole('dialog', { name: 'Edit Planned Route' }),
  ).toBeNull();
  const dialog = screen.getByRole('dialog', {
    name: 'Usage of Bracket std v3',
  });
  expect(dialog.textContent).toContain('QF-0140');
  expect(dialog.textContent).toContain('2027-60-8114-00');
  expect(dialog.textContent).toContain('keeps its own route snapshot');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
});

test('editing a used route states that only future assignments change', () => {
  renderPlannedRoutes();

  const dialog = openEdit('Bracket std v3');
  expect(dialog.textContent).toContain('future assignments only');

  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket std v4' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));

  expect(routeRow('Bracket std v4')).toBeTruthy();
  // The usage (existing snapshots) is untouched by the edit.
  expect(
    within(routeRow('Bracket std v4')).getByRole('button', {
      name: /2 Quantity Flows/,
    }),
  ).toBeInTheDocument();
});

test('step Operation and Preferred Machine are selects scoped to the Area', () => {
  renderPlannedRoutes();
  const dialog = openEdit('Milled housing + plating');

  // Step 3 (Mill): Operation from the Area's Operations, preferred
  // Machine referenced by its stable id and displayed by name.
  const op3 = within(dialog).getByLabelText(
    'Step 3 Operation',
  ) as HTMLSelectElement;
  const machine3 = within(dialog).getByLabelText(
    'Step 3 preferred Machine',
  ) as HTMLSelectElement;
  expect(op3).toHaveValue('Milling');
  expect(machine3).toHaveValue('MC-302');
  expect(machine3.selectedOptions[0].textContent).toBe('Mill 2');

  // Only ACTIVE Machines of the Area are offered: retired MC-202
  // `Saw 2` never appears for the Cut step.
  const machine2 = within(dialog).getByLabelText(
    'Step 2 preferred Machine',
  ) as HTMLSelectElement;
  expect(Array.from(machine2.options, (o) => o.textContent)).toEqual([
    '— no preferred Machine',
    'Saw 1',
  ]);

  // Changing the Area resets the Operation to the new Area's Operations
  // and clears a preferred Machine that does not belong to it.
  fireEvent.change(within(dialog).getByLabelText('Step 3 Area'), {
    target: { value: 'lathe' },
  });
  expect(op3).toHaveValue('Turning');
  expect(Array.from(op3.options, (o) => o.value)).toEqual(['Turning']);
  expect(machine3).toHaveValue('');
  expect(Array.from(machine3.options, (o) => o.textContent)).toEqual([
    '— no preferred Machine',
    'Lathe 1',
    'Lathe 2',
    'Lathe 3',
    'Lathe 4',
  ]);

  // Rows drag to reorder, with ↑/↓ kept as the keyboard/touch path.
  expect(dialog.querySelector('.rt-steprow')).toHaveAttribute(
    'draggable',
    'true',
  );
  expect(
    within(dialog).getByRole('button', { name: 'Move step 3 up' }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Move step 3 down' }),
  ).toBeInTheDocument();
});

test('a used route archives from its edit dialog after typing the route name', () => {
  renderPlannedRoutes();

  const dialog = openEdit('Milled housing + plating');
  // Used route: Archive… is offered, Delete… is not.
  expect(within(dialog).queryByRole('button', { name: 'Delete…' })).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));

  const archiveDialog = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  expect(archiveDialog.textContent).toContain(
    'no longer appears as a choice for future assignments',
  );
  expect(archiveDialog.textContent).toContain(
    'keep their Assigned Route snapshot unchanged',
  );

  // The confirming action stays disabled until the exact route name is
  // typed (trim + case-insensitive deliberate acknowledgement).
  const confirmButton = within(archiveDialog).getByRole('button', {
    name: 'Archive route',
  });
  const gate = within(archiveDialog).getByLabelText(/to confirm$/);
  expect(confirmButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: 'Milled housing' } });
  expect(confirmButton).toBeDisabled();
  fireEvent.change(gate, {
    target: { value: '  milled housing + plating ' },
  });
  expect(confirmButton).toBeEnabled();
  fireEvent.click(confirmButton);

  const archivedRow = routeRow('Milled housing + plating');
  expect(archivedRow.className).toContain('archived');
  expect(archivedRow.closest('.rt-archived')).not.toBeNull();
});

test('a never-used route deletes outright from its edit dialog', () => {
  renderPlannedRoutes();

  const dialog = openEdit('Lathe + mill combo (trial)');
  // Never used: Delete… is offered, Archive… is not.
  expect(within(dialog).queryByRole('button', { name: 'Archive…' })).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete…' }));

  const deleteDialog = screen.getByRole('dialog', {
    name: 'Delete Planned Route',
  });
  expect(deleteDialog.textContent).toContain('never been used');
  fireEvent.click(
    within(deleteDialog).getByRole('button', { name: 'Delete route' }),
  );
  expect(document.body.textContent).not.toContain('Lathe + mill combo (trial)');
});

test('archiving with unsaved edits requires an explicit Save / Discard decision first', () => {
  renderPlannedRoutes();

  const dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket std v4' },
  });
  expect(dialog.textContent).toContain('● Unsaved changes');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));

  // The unsaved-edit gate comes before the typed confirmation.
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeInTheDocument();
  expect(
    within(choice).getByRole('button', { name: 'Discard changes' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(choice).getByRole('button', { name: 'Save changes, then archive' }),
  );

  // The typed confirmation expects the just-saved name.
  const archiveDialog = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  expect(archiveDialog.textContent).toContain(
    'Type Bracket std v4 (route name) to confirm',
  );
  fireEvent.change(within(archiveDialog).getByLabelText(/to confirm$/), {
    target: { value: 'bracket std v4' },
  });
  fireEvent.click(
    within(archiveDialog).getByRole('button', { name: 'Archive route' }),
  );

  const archivedRow = routeRow('Bracket std v4');
  expect(archivedRow.className).toContain('archived');
});

test('duplicating with unsaved edits offers duplicating the saved route instead', () => {
  renderPlannedRoutes();

  const dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket experimental' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));

  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Save, then duplicate' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(choice).getByRole('button', { name: 'Duplicate the saved route' }),
  );

  // The duplicate is based on the saved route, not the abandoned edit,
  // and opens for editing.
  const editCopy = screen.getByRole('dialog', { name: 'Edit Planned Route' });
  expect(within(editCopy).getByLabelText('Route name')).toHaveValue(
    'Bracket std v3 (variant)',
  );
  fireEvent.click(
    within(editCopy).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  // The original keeps its saved name.
  expect(routeRow('Bracket std v3')).toBeTruthy();
});

test('closing a dirty route dialog asks before discarding', () => {
  renderPlannedRoutes();

  const dialog = openEdit('Lathe + mill combo (trial)');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Trial v2' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // Nothing is silently discarded: an explicit confirmation appears.
  const confirm = screen.getByRole('dialog', {
    name: 'Discard unsaved route changes?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep editing' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Edit Planned Route' }),
  ).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Route name')).toHaveValue('Trial v2');

  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(routeRow('Lathe + mill combo (trial)')).toBeTruthy();
});

test('duplication creates an active never-used variant and opens it for editing', () => {
  renderPlannedRoutes();

  fireEvent.click(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Edit Planned Route' });
  expect(
    (within(dialog).getByLabelText('Route name') as HTMLInputElement).value,
  ).toBe('Legacy plating route (variant)');
  // A fresh variant has no usage, so no future-assignments warning.
  expect(dialog.textContent).not.toContain('future assignments only');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));

  const variant = routeRow('Legacy plating route (variant)');
  expect(variant.querySelector('.rt-status')?.textContent).toBe('Active');
  expect(variant.textContent).toContain('Never used');
});

test('a new route requires a name and complete steps', () => {
  renderPlannedRoutes();

  fireEvent.click(screen.getByRole('button', { name: '+ New Planned Route' }));
  const dialog = screen.getByRole('dialog', { name: 'New Planned Route' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'route name is required',
  );

  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Deburr-only rework path' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: '+ Add step' }));
  // The Operation select follows the chosen Area's Operations.
  fireEvent.change(within(dialog).getByLabelText('Step 2 Area'), {
    target: { value: 'deburr' },
  });
  expect(within(dialog).getByLabelText('Step 2 Operation')).toHaveValue(
    'Deburring',
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));

  const row = routeRow('Deburr-only rework path');
  expect(row.querySelector('.rt-status')?.textContent).toBe('Active');
  expect(row.textContent).toContain('Never used');
});

/* ============ Offline write-block ============ */

test('offline disables New Planned Route and archived-row Duplicate; reading stays available', () => {
  renderPlannedRoutes('unavailable');

  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeDisabled();
  expect(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  ).toBeDisabled();

  // Read-only/search/navigation stay available offline: the row still
  // opens the edit dialog.
  const dialog = openEdit('Bracket std v3');
  expect(dialog).toBeInTheDocument();
});

test('offline disables Save/Duplicate/Archive…/Delete… inside the route dialog', () => {
  renderPlannedRoutes('unavailable');

  const used = openEdit('Bracket std v3');
  expect(
    within(used).getByRole('button', { name: 'Save route' }),
  ).toBeDisabled();
  expect(
    within(used).getByRole('button', { name: 'Duplicate' }),
  ).toBeDisabled();
  expect(within(used).getByRole('button', { name: 'Archive…' })).toBeDisabled();
  fireEvent.click(within(used).getByRole('button', { name: 'Cancel (Esc)' }));

  const neverUsed = openEdit('Lathe + mill combo (trial)');
  expect(
    within(neverUsed).getByRole('button', { name: 'Delete…' }),
  ).toBeDisabled();
});

test('reconnecting re-enables the write actions', () => {
  const { rerender } = renderPlannedRoutes('unavailable');
  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeDisabled();

  rerender(
    <ConnectivityContext.Provider
      value={{ status: 'connected', retry: vi.fn() }}
    >
      <PlannedRoutesView />
    </ConnectivityContext.Provider>,
  );
  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeEnabled();
});

test('offline mid-flow disables the Archive workflow’s typed-confirm even once the name matches — nothing archives', () => {
  // The realistic sequence: open the workflow while CONNECTED, then
  // lose connectivity with it already open — entry-point blocking
  // (Archive… disabled) alone would miss this gap.
  const { rerender } = renderPlannedRoutes('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <PlannedRoutesView />
      </ConnectivityContext.Provider>,
    );

  const dialog = openEdit('Bracket std v3');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));
  const archiveDialog = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  fireEvent.change(within(archiveDialog).getByLabelText(/to confirm$/), {
    target: { value: 'Bracket std v3' },
  });
  const confirmButton = within(archiveDialog).getByRole('button', {
    name: 'Archive route',
  });
  expect(confirmButton).toBeEnabled();

  // Connectivity drops with the typed-confirm dialog already open and
  // already satisfied — the shared TypedConfirmDialog must still gate
  // its own confirming action on the external writeBlocked prop.
  reconnectAs('unavailable');
  const stillArchive = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  const stillDisabled = within(stillArchive).getByRole('button', {
    name: 'Archive route',
  });
  expect(stillDisabled).toBeDisabled();
  fireEvent.click(stillDisabled);

  // Nothing mutated: the route is still active, not archived.
  expect(
    screen.getByRole('dialog', { name: 'Archive Planned Route' }),
  ).toBeInTheDocument();
  expect(routeRow('Bracket std v3').closest('.rt-archived')).toBeNull();
});

test('offline mid-flow disables both Save and Discard in the Duplicate unsaved-changes dialog — nothing is duplicated', () => {
  // Duplicate's Discard branch still writes (it duplicates from the
  // saved baseline) — the shared UnsavedChoiceDialog's discardDisabled
  // prop covers that, unlike ordinary Discard-just-closes flows.
  const { rerender } = renderPlannedRoutes('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <PlannedRoutesView />
      </ConnectivityContext.Provider>,
    );

  const dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket experimental' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Save, then duplicate' }),
  ).toBeEnabled();
  expect(
    within(choice).getByRole('button', {
      name: 'Duplicate the saved route',
    }),
  ).toBeEnabled();

  reconnectAs('unavailable');
  const stillChoice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(stillChoice).getByRole('button', {
      name: 'Save, then duplicate',
    }),
  ).toBeDisabled();
  expect(
    within(stillChoice).getByRole('button', {
      name: 'Duplicate the saved route',
    }),
  ).toBeDisabled();

  // No duplicate was created under either name.
  expect(screen.queryByText('Bracket std v3 (variant)')).toBeNull();
  expect(screen.queryByText('Bracket experimental (variant)')).toBeNull();
});

/* ============ ?state=long ============ */

test('?state=long renders many long-name/description routes with a long step chain', () => {
  window.history.replaceState({}, '', '/management/planned-routes?state=long');
  renderPlannedRoutes();

  // Sample data is still present…
  expect(routeRow('Bracket std v3')).toBeTruthy();
  // …plus the long-preview rows, including the over-long name.
  const supplemental = routeRow(
    'Supplemental long-preview route — multi-stage housing assembly with outside plating, secondary deburr, and final inspection rework loop',
  );
  expect(supplemental.textContent).toContain(
    'Long-data preview: an over-long route name and description',
  );
  // The long step chain renders every step chip.
  expect(supplemental.querySelectorAll('.rt-steps .rt-stepchip').length).toBe(
    10,
  );
  expect(document.body.textContent).toContain(
    'Long preview route variant 1 — extended qualification cell',
  );
});
