import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../../app/connectivity-context';
import { PartNumbersView } from './PartNumbersView';

// Management → Part Numbers (GUI_DESIGN §14): PartNumber master
// metadata maintenance. The canonical PN string is the identity —
// records are optional metadata only, the barcode is always the
// derived `PF:PN:<part-number>`, the default image is the ONE shared
// PN placeholder, and deletion removes nothing but the metadata
// record.

beforeEach(() => {
  window.history.replaceState({}, '', '/management/part-numbers');
});

afterEach(cleanup);

/** Render Part Numbers with a fixed connectivity status —
 * deterministic, no fetch/timer polling. Defaults to `connected` so
 * the existing behavioral tests exercise a fully-enabled view;
 * offline-specific tests pass `'unavailable'` explicitly. */
function renderPartNumbers(status: 'connected' | 'unavailable' = 'connected') {
  return render(
    <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
      <PartNumbersView />
    </ConnectivityContext.Provider>,
  );
}

/** The list row whose PN cell names the record. */
function row(pn: string): HTMLElement {
  const table = document.querySelector('.pnm-table');
  expect(table).not.toBeNull();
  const match = Array.from(table!.querySelectorAll('tbody tr')).find(
    (tr) => tr.querySelector('.pnm-pn')?.textContent === pn,
  );
  expect(match).toBeDefined();
  return match as HTMLElement;
}

/** Open Edit Part Number through the whole-row click. */
function openEdit(pn: string): HTMLElement {
  fireEvent.click(row(pn));
  return screen.getByRole('dialog', { name: 'Edit Part Number' });
}

test('the list shows master records with canonical PN, metadata and derived barcode', () => {
  renderPartNumbers();

  const bracket = row('2027-60-8114-00');
  expect(bracket.textContent).toContain(
    'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
  );
  expect(bracket.textContent).toContain('C');
  expect(bracket.textContent).toContain('ERP-PN-40412');
  // The barcode column carries the derived value — PF:PN: + canonical PN.
  expect(bracket.querySelector('.barcodeval')?.textContent).toBe(
    'PF:PN:2027-60-8114-00',
  );

  // Absent optional metadata renders as `—`, never as an error.
  const spacer = row('214-406');
  expect(within(spacer).getAllByText('—').length).toBeGreaterThanOrEqual(2);

  // Every sample record uses the ONE shared default placeholder — no
  // uploaded image exists in the sample data, so no <img> renders.
  expect(document.querySelectorAll('.pnm-table img')).toHaveLength(0);
  expect(
    document.querySelectorAll('.pnm-table .pn-img').length,
  ).toBeGreaterThan(3);

  // A PN whose master was hard-deleted (Tracking: TEST-SCRAP-PLATE)
  // deliberately has no record here.
  expect(screen.queryByText('TEST-SCRAP-PLATE')).toBeNull();
});

test('search filters over PN, name, revision and ERP id', () => {
  renderPartNumbers();

  fireEvent.change(screen.getByLabelText('Search Part Numbers'), {
    target: { value: 'bracket' },
  });
  expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(1);
  expect(row('2027-60-8114-00')).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Search Part Numbers'), {
    target: { value: 'no-such-part' },
  });
  expect(
    screen.getByText('No saved Part Number details match “no-such-part”.'),
  ).toBeInTheDocument();
});

test('the whole row opens Edit with the read-only identity header', () => {
  renderPartNumbers();

  const dialog = openEdit('2027-60-8114-00');
  const identity = dialog.querySelector('.pnm-idhead') as HTMLElement;
  expect(identity).not.toBeNull();
  // PN and derived barcode are read-only values — no PN input exists
  // on an existing record, and the barcode is never editable.
  expect(identity.textContent).toContain('2027-60-8114-00');
  expect(identity.textContent).toContain('PF:PN:2027-60-8114-00');
  expect(within(dialog).queryByLabelText('Part Number')).toBeNull();
  expect(
    within(identity).getByRole('button', { name: 'Barcode label…' }),
  ).toBeInTheDocument();

  // Metadata fields arrive prefilled.
  expect(within(dialog).getByLabelText(/Name \/ Description/)).toHaveValue(
    'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
  );
  expect(within(dialog).getByLabelText(/Revision/)).toHaveValue('C');
  expect(within(dialog).getByLabelText(/ERP ID/)).toHaveValue('ERP-PN-40412');

  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('metadata edits save to the row; closing dirty asks first', () => {
  renderPartNumbers();

  const dialog = openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  expect(within(dialog).getByText('● Unsaved changes')).toBeInTheDocument();

  // A close request with unsaved input never discards silently.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(within(choice).getByRole('button', { name: 'Save changes' }));

  expect(screen.queryByRole('dialog')).toBeNull();
  expect(row('142-260').textContent).toContain('B');

  // The saved value round-trips into the reopened dialog.
  const reopened = openEdit('142-260');
  expect(within(reopened).getByLabelText(/Revision/)).toHaveValue('B');
});

test('a new Part Number is canonicalized; internal whitespace and duplicates are field errors', () => {
  renderPartNumbers();

  fireEvent.click(screen.getByRole('button', { name: '+ New Part Number' }));
  const dialog = screen.getByRole('dialog', { name: 'New Part Number' });
  const pnField = within(dialog).getByLabelText('Part Number');

  // Internal whitespace is rejected with an inline explanation — never
  // silently removed.
  fireEvent.change(pnField, { target: { value: 'ABC 123' } });
  expect(dialog.textContent).toContain(
    'Part Number cannot contain spaces or other whitespace.',
  );

  // An existing canonical PN (entered lowercase) is a duplicate —
  // one master record per canonical PN.
  fireEvent.change(pnField, { target: { value: '142-260' } });
  expect(dialog.textContent).toContain(
    'Part Number “142-260” already has saved details.',
  );

  // A valid lowercase entry shows the canonical PN and its derived
  // barcode before anything is added.
  fireEvent.change(pnField, { target: { value: '  abc-123 ' } });
  expect(dialog.textContent).toContain('Will be saved as');
  expect(dialog.textContent).toContain('ABC-123');
  expect(dialog.textContent).toContain('PF:PN:ABC-123');

  fireEvent.change(within(dialog).getByLabelText(/Name \/ Description/), {
    target: { value: 'SAMPLE, TEST PART' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );

  // The new record renders canonical UPPERCASE with the derived
  // barcode and the shared default placeholder image.
  expect(screen.queryByRole('dialog')).toBeNull();
  const added = row('ABC-123');
  expect(added.querySelector('.barcodeval')?.textContent).toBe('PF:PN:ABC-123');
  expect(added.querySelector('img')).toBeNull();
  expect(added.querySelector('.pn-img')).not.toBeNull();
});

test('an empty PN blocks adding with a field error', () => {
  renderPartNumbers();

  fireEvent.click(screen.getByRole('button', { name: '+ New Part Number' }));
  const dialog = screen.getByRole('dialog', { name: 'New Part Number' });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );
  expect(dialog.textContent).toContain('A Part Number is required.');
  // Nothing was added; the dialog stays open.
  expect(
    screen.getByRole('dialog', { name: 'New Part Number' }),
  ).toBeInTheDocument();
});

test('uploading a custom image replaces the placeholder; removing restores it', async () => {
  renderPartNumbers();

  const dialog = openEdit('309-127');
  // The dialog starts on the shared default placeholder.
  expect(dialog.querySelector('img.pn-img')).toBeNull();
  expect(dialog.querySelector('span.pn-img')).not.toBeNull();

  const file = new File(['pn-image-bytes'], 'pin.png', { type: 'image/png' });
  fireEvent.change(within(dialog).getByLabelText('Upload image'), {
    target: { files: [file] },
  });

  // FileReader resolves asynchronously to a data URL preview.
  await waitFor(() =>
    expect(
      within(dialog).getByAltText('Part image — 309-127'),
    ).toBeInTheDocument(),
  );

  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  // The saved image renders in the list row instead of the placeholder.
  expect(
    within(row('309-127')).getByAltText('Part image — 309-127'),
  ).toBeInTheDocument();

  // Removing the image returns to the ONE shared default placeholder.
  const reopened = openEdit('309-127');
  fireEvent.click(
    within(reopened).getByRole('button', { name: 'Remove image' }),
  );
  expect(reopened.querySelector('img.pn-img')).toBeNull();
  fireEvent.click(
    within(reopened).getByRole('button', { name: 'Save changes' }),
  );
  expect(row('309-127').querySelector('img')).toBeNull();
  expect(row('309-127').querySelector('.pn-img')).not.toBeNull();
});

test('the barcode label dialog renders the scannable PN barcode with the PN beneath', () => {
  renderPartNumbers();

  const dialog = openEdit('118-052');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Barcode label…' }),
  );
  const label = screen.getByRole('dialog', {
    name: 'Part Number barcode label',
  });
  // Simple label: the Code 128 barcode with the PN text beneath it and
  // the full scanned value as the verification line.
  const svg = label.querySelector('svg.lbarcode') as SVGElement;
  expect(svg).not.toBeNull();
  expect(svg.getAttribute('aria-label')).toBe('Barcode PF:PN:118-052');
  expect(svg.querySelectorAll('rect').length).toBeGreaterThan(20);
  expect(label.querySelector('.lpn')?.textContent).toBe('118-052');
  expect(label.querySelector('.lvalue')?.textContent).toBe('PF:PN:118-052');
  expect(
    within(label).getByRole('button', { name: 'Print Label' }),
  ).toBeInTheDocument();

  // Cancel returns to the Edit dialog.
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.getByRole('dialog', { name: 'Edit Part Number' }),
  ).toBeInTheDocument();
});

test('deleting a record removes only the metadata row after an explicit confirmation', () => {
  renderPartNumbers();

  const before = document.querySelectorAll('.pnm-table tbody tr').length;
  const dialog = openEdit('214-406');
  const zone = dialog.querySelector('.pnm-dangerzone') as HTMLElement;
  expect(zone.textContent).toContain('Delete Part Number Details');
  expect(zone.textContent).toContain(
    'Production tracking and Work Order history are not affected.',
  );

  fireEvent.click(
    within(zone).getByRole('button', { name: 'Delete details…' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Delete Part Number details?',
  });
  // The final confirmation is the strongest warning in the flow: the
  // shared attention confirmation variant in the danger tone (the
  // Machines final-question presentation) — badge, danger-toned title
  // and a danger-toned confirming action; still one plain destructive
  // confirmation, never a typed gate. The delete section above keeps
  // its lighter danger-zone treatment.
  expect(confirm.className).toContain('alertdlg');
  expect(confirm.className).toContain('tone-danger');
  expect(confirm.querySelector('.alertbadge')).not.toBeNull();
  expect(
    within(confirm).getByRole('button', { name: 'Delete details' }),
  ).toHaveClass('danger');
  expect(confirm.querySelector('.typedconfirm')).toBeNull();
  // The confirmation states the scope: saved details only — the PN and
  // its production history stay available.
  expect(confirm.textContent).toContain('permanently removes the saved image');
  expect(confirm.textContent).toContain('production history remain available');

  // Cancel changes nothing.
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(before);

  // Confirming really deletes the one record.
  const again = openEdit('214-406');
  fireEvent.click(
    within(again.querySelector('.pnm-dangerzone') as HTMLElement).getByRole(
      'button',
      { name: 'Delete details…' },
    ),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Delete Part Number details?' }),
    ).getByRole('button', { name: 'Delete details' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(
    before - 1,
  );
  expect(screen.queryByText('214-406')).toBeNull();
  // Other records are unaffected.
  expect(row('2027-60-8114-00')).toBeInTheDocument();
});

test('discarding a new Part Number asks before dropping entered input', () => {
  renderPartNumbers();

  fireEvent.click(screen.getByRole('button', { name: '+ New Part Number' }));
  const dialog = screen.getByRole('dialog', { name: 'New Part Number' });
  fireEvent.change(within(dialog).getByLabelText('Part Number'), {
    target: { value: 'new-part-01' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));

  const confirm = screen.getByRole('dialog', {
    name: 'Discard new Part Number?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Discard input' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.queryByText('NEW-PART-01')).toBeNull();
});

/* ============ Offline write-block ============ */

test('offline disables New Part Number; reading stays available', () => {
  renderPartNumbers('unavailable');

  expect(
    screen.getByRole('button', { name: '+ New Part Number' }),
  ).toBeDisabled();

  // Read-only/search/navigation stay available offline: the row still
  // opens Edit Part Number.
  const dialog = openEdit('2027-60-8114-00');
  expect(dialog).toBeInTheDocument();
});

test('offline disables Save changes and Delete details… inside the edit dialog', () => {
  renderPartNumbers('unavailable');

  const dialog = openEdit('214-406');
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  const zone = dialog.querySelector('.pnm-dangerzone') as HTMLElement;
  expect(
    within(zone).getByRole('button', { name: 'Delete details…' }),
  ).toBeDisabled();
});

test('reconnecting re-enables the write actions', () => {
  const { rerender } = renderPartNumbers('unavailable');
  expect(
    screen.getByRole('button', { name: '+ New Part Number' }),
  ).toBeDisabled();

  rerender(
    <ConnectivityContext.Provider
      value={{ status: 'connected', retry: vi.fn() }}
    >
      <PartNumbersView />
    </ConnectivityContext.Provider>,
  );
  expect(
    screen.getByRole('button', { name: '+ New Part Number' }),
  ).toBeEnabled();
});

test('offline mid-flow disables the delete confirmation — the record survives', () => {
  // The realistic sequence: open the workflow while CONNECTED, then
  // lose connectivity with it already open — entry-point blocking
  // (Delete details… disabled) alone would miss this gap.
  const { rerender } = renderPartNumbers('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <PartNumbersView />
      </ConnectivityContext.Provider>,
    );

  const before = document.querySelectorAll('.pnm-table tbody tr').length;
  const dialog = openEdit('214-406');
  const zone = dialog.querySelector('.pnm-dangerzone') as HTMLElement;
  fireEvent.click(
    within(zone).getByRole('button', { name: 'Delete details…' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Delete Part Number details?',
  });
  const deleteButton = within(confirm).getByRole('button', {
    name: 'Delete details',
  });
  expect(deleteButton).toBeEnabled();

  reconnectAs('unavailable');
  const stillConfirm = screen.getByRole('dialog', {
    name: 'Delete Part Number details?',
  });
  const stillDisabled = within(stillConfirm).getByRole('button', {
    name: 'Delete details',
  });
  expect(stillDisabled).toBeDisabled();
  fireEvent.click(stillDisabled);

  // Nothing mutated: still open, and the record is still in the table.
  expect(
    screen.getByRole('dialog', { name: 'Delete Part Number details?' }),
  ).toBeInTheDocument();
  expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(before);
  expect(row('214-406')).toBeInTheDocument();
});

test('offline mid-flow disables the unsaved-edits Save changes but keeps Discard changes available — nothing is saved', () => {
  const { rerender } = renderPartNumbers('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <PartNumbersView />
      </ConnectivityContext.Provider>,
    );

  const dialog = openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Save changes' }),
  ).toBeEnabled();

  reconnectAs('unavailable');
  const stillChoice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  // Save writes immediately here (it calls onSave directly) — it must
  // gate on writeBlocked. Discard never persists anything — it must
  // keep working offline.
  expect(
    within(stillChoice).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  expect(
    within(stillChoice).getByRole('button', { name: 'Discard changes' }),
  ).toBeEnabled();

  fireEvent.click(
    within(stillChoice).getByRole('button', { name: 'Discard changes' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(row('142-260').textContent).not.toContain('B');

  const reopened = openEdit('142-260');
  expect(within(reopened).getByLabelText(/Revision/)).toHaveValue('A');
});

/* ============ ?state=long ============ */

test('?state=long renders many long-PN/name/metadata records alongside the sample data', () => {
  window.history.replaceState({}, '', '/management/part-numbers?state=long');
  renderPartNumbers();

  // Sample data is still present…
  expect(row('2027-60-8114-00')).toBeTruthy();
  // …plus the long-preview records, including the over-long PN, name
  // and metadata.
  const supplemental = row(
    '0118-40-0022-07-0455-88-REV-C-SUPPLEMENTAL-LONG-PREVIEW',
  );
  expect(supplemental.textContent).toContain(
    'SUPPLEMENTAL LONG-PREVIEW PART NUMBER',
  );
  expect(supplemental.textContent).toContain('REV-SUPPLEMENTAL-LONG');
  expect(supplemental.textContent).toContain(
    'ERP-PN-40412-SUPPLEMENTAL-AMENDMENT-2026-REV-B-LONG-PREVIEW',
  );
  expect(document.body.textContent).toContain('0114-60-0101-00');
});
