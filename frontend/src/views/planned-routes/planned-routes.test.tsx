import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';

import { PlannedRoutesView } from './PlannedRoutesView';

// Management → Planned Routes (GUI_DESIGN §13): reusable route
// definitions with the RouteTemplate semantics — edits affect future
// assignments only, used routes archive instead of deleting, archived
// routes stay visible but are no normal choice for new assignments.

beforeEach(() => {
  window.history.replaceState({}, '', '/management/planned-routes');
});

afterEach(cleanup);

function routeRow(name: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('.rt-table tbody tr')).find(
    (tr) => tr.querySelector('.rtname')?.textContent === name,
  );
  expect(row).toBeDefined();
  return row as HTMLElement;
}

test('routes list their step sequence and active/archived status distinctly', () => {
  render(<PlannedRoutesView />);

  const bracket = routeRow('Bracket std v3');
  expect(
    Array.from(bracket.querySelectorAll('.rt-steps .stp'), (el) =>
      el.textContent?.replace('→', '').trim(),
    ),
  ).toEqual(['Material', 'Cut', 'Lathe', 'Deburr', 'Stockroom']);
  expect(bracket.querySelector('.rt-status')?.textContent).toBe('Active');

  const legacy = routeRow('Legacy plating route');
  expect(legacy.className).toContain('archived');
  expect(legacy.querySelector('.rt-status')?.textContent).toBe('Archived');
  // Archived routes offer no edit/archive/delete — only duplication
  // into a fresh active route.
  expect(within(legacy).queryByRole('button', { name: 'Edit…' })).toBeNull();
  expect(
    within(legacy).getByRole('button', { name: 'Duplicate' }),
  ).toBeInTheDocument();
});

test('usage inspection lists the Quantity Flows released with the route', () => {
  render(<PlannedRoutesView />);

  fireEvent.click(
    within(routeRow('Bracket std v3')).getByRole('button', {
      name: /2 Quantity Flows/,
    }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Usage of Bracket std v3',
  });
  expect(dialog.textContent).toContain('QF-0140');
  expect(dialog.textContent).toContain('2027-60-8114-00');
  expect(dialog.textContent).toContain('keeps its own route snapshot');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
});

test('editing a used route states that only future assignments change', () => {
  render(<PlannedRoutesView />);

  fireEvent.click(
    within(routeRow('Bracket std v3')).getByRole('button', { name: 'Edit…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Edit Planned Route' });
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

test('a used route archives instead of deleting; a never-used route deletes', () => {
  render(<PlannedRoutesView />);

  // Used route: only Archive… is offered.
  const used = routeRow('Milled housing + plating');
  expect(within(used).queryByRole('button', { name: 'Delete…' })).toBeNull();
  fireEvent.click(within(used).getByRole('button', { name: 'Archive…' }));
  const archiveDialog = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  expect(archiveDialog.textContent).toContain(
    'stops appearing as a choice for new route assignments',
  );
  fireEvent.click(
    within(archiveDialog).getByRole('button', { name: 'Archive route' }),
  );
  expect(routeRow('Milled housing + plating').className).toContain('archived');

  // Never-used route: Delete… exists and removes it completely.
  const trial = routeRow('Lathe + mill combo (trial)');
  expect(within(trial).queryByRole('button', { name: 'Archive…' })).toBeNull();
  fireEvent.click(within(trial).getByRole('button', { name: 'Delete…' }));
  const deleteDialog = screen.getByRole('dialog', {
    name: 'Delete Planned Route',
  });
  expect(deleteDialog.textContent).toContain('never been used');
  fireEvent.click(
    within(deleteDialog).getByRole('button', { name: 'Delete route' }),
  );
  expect(document.body.textContent).not.toContain('Lathe + mill combo (trial)');
});

test('duplication creates an active never-used variant and opens it for editing', () => {
  render(<PlannedRoutesView />);

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
  render(<PlannedRoutesView />);

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
  fireEvent.change(within(dialog).getByLabelText('Step 2 Operation'), {
    target: { value: 'Deburring' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));

  const row = routeRow('Deburr-only rework path');
  expect(row.querySelector('.rt-status')?.textContent).toBe('Active');
  expect(row.textContent).toContain('Never used');
});
