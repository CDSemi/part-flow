import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Work Orders regression tests: the Work Order Details modal dialog
// over the always-mounted list (no URL change), the New Work Order
// modal workflow (optional WO Number — a blank number saves a NULL
// number rendered as `—`, no temporary number generation — and
// optional due dates, missing-information Save Demand confirmation),
// the multi-step Add Part dialog and its stacked-dialog behavior,
// PN-carrying barcodes with create-on-first-use, OPEN Work Order
// editing, nullable due-date behavior, mock validation, and
// unsaved-change protection. Everything here exercises Phase 2
// development mock state only.

beforeEach(() => {
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
  vi.restoreAllMocks();
});

async function renderWorkOrders() {
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
}

function openNewWorkOrderDialog() {
  const button = screen.getByRole('button', { name: '＋ New Work Order' });
  // A real click focuses the button first; jsdom needs this explicitly
  // so focus restoration on close can be asserted.
  button.focus();
  fireEvent.click(button);
  return screen.getByRole('dialog', { name: 'New Work Order' });
}

function scanBarcode(barcode: string) {
  const scan = screen.getByLabelText('Scan PN barcode');
  fireEvent.change(scan, { target: { value: barcode } });
  fireEvent.keyDown(scan, { key: 'Enter' });
  return scan;
}

function workOrderRow(workOrderNumber: string) {
  return screen.getByRole('button', { name: new RegExp(workOrderNumber) });
}

async function openWorkOrderDetail(workOrderNumber: string) {
  const row = workOrderRow(workOrderNumber);
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  expect(dialog).toHaveTextContent(workOrderNumber);
  return dialog;
}

/* ============ Work Order Details modal ============ */

test('clicking a Work Order row opens the Work Order Details dialog over the list', async () => {
  await renderWorkOrders();

  const dialog = await openWorkOrderDetail('007010');

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  // The URL never changes — the details are a dialog, not a route.
  expect(window.location.pathname).toBe('/management/work-orders');
  // The Work Order list stays mounted and visible behind the overlay.
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
  expect(document.querySelector('.wolist')).toBeInTheDocument();
  // Primary dialog actions.
  expect(
    within(dialog).getByRole('button', { name: 'Save demand' }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeInTheDocument();
});

test('the toolbar hosts ＋ New Work Order and the whole row opens the details dialog', async () => {
  await renderWorkOrders();

  // The primary action sits in the toolbar row beside the search field
  // (v15) — same layout as Machines.
  const newButton = screen.getByRole('button', { name: '＋ New Work Order' });
  expect(newButton.closest('.wo-tools')).not.toBeNull();

  // The name-cell button keeps the accessible entry point while the
  // COMPLETE row is clickable.
  const rowButton = screen.getByRole('button', {
    name: 'Open Work Order 007010',
  });
  const row = rowButton.closest('tr') as HTMLTableRowElement;
  expect(row.className).toContain('selrow');

  // Clicking outside the name button — the Status cell — opens the
  // details dialog too.
  fireEvent.click(row.cells[row.cells.length - 1]);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  expect(dialog).toHaveTextContent('007010');
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a clean Work Order Details dialog closes directly and focus returns to its row', async () => {
  await renderWorkOrders();

  // Escape on a clean dialog closes without any confirmation.
  const dialog = await openWorkOrderDetail('007010');
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(workOrderRow('007010'));

  // Cancel (Esc) behaves identically.
  const reopened = await openWorkOrderDetail('007010');
  fireEvent.click(
    within(reopened).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(workOrderRow('007010'));
});

test('closing a dirty Work Order Details dialog requires explicit discard confirmation', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007010');
  const qty = within(dialog).getByLabelText('Quantity for 0455-20-0118-03');
  fireEvent.change(qty, { target: { value: '99' } });

  // Escape never discards silently: an explicit confirmation appears.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(
    screen.getByRole('dialog', { name: 'Discard unsaved demand changes?' }),
  ).toBeInTheDocument();

  // Cancelling the discard keeps the dialog and every entered value.
  fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByLabelText('Quantity for 0455-20-0118-03'),
  ).toHaveValue('99');

  // A backdrop click is guarded exactly the same way.
  fireEvent.mouseDown(dialog.parentElement!);
  expect(
    screen.getByRole('dialog', { name: 'Discard unsaved demand changes?' }),
  ).toBeInTheDocument();

  // Confirming the discard closes Work Order Details.
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('Escape and backdrop on the Add Part child dialog close only the child', async () => {
  await renderWorkOrders();
  const details = await openWorkOrderDetail('007010');
  fireEvent.click(
    within(details).getByRole('button', { name: '＋ Add Part manually' }),
  );

  // Escape on the child closes the child, never Work Order Details.
  const child = screen.getByRole('dialog', { name: /Add Part — step 1/ });
  fireEvent.keyDown(child, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();

  // A backdrop mousedown on the child closes only the child too.
  fireEvent.click(
    within(details).getByRole('button', { name: '＋ Add Part manually' }),
  );
  const child2 = screen.getByRole('dialog', { name: /Add Part — step 1/ });
  fireEvent.mouseDown(child2.parentElement!);
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('the Add Part quantity step renders its own keypad when Work Orders mounts directly', async () => {
  // No Scan Station render first: the keypad presentation is owned by
  // components/QuantityKeypad (its CSS ships with the component), so
  // the structure must be complete with Work Orders mounted alone.
  await renderWorkOrders();
  const details = await openWorkOrderDetail('007010');
  fireEvent.click(
    within(details).getByRole('button', { name: '＋ Add Part manually' }),
  );
  const addPart = screen.getByRole('dialog', { name: /Add Part — step 1/ });

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '309' },
  });
  fireEvent.click(screen.getByRole('button', { name: /309-127/ }));
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();

  // The focused numeric input and the full keypad are present; MAX is
  // absent because Add Part provides no maximum.
  const display = addPart.querySelector('input.qtydisplay');
  expect(display).toBeInTheDocument();
  expect(document.activeElement).toBe(display);
  expect(addPart.querySelectorAll('.keypad button')).toHaveLength(12);
  expect(addPart.querySelector('.keypad button.keypad-max')).toBeNull();

  // Physical keys reach the quantity; completing the flow adds the
  // draft line to the still-open Work Order Details dialog.
  fireEvent.keyDown(addPart, { key: '5' });
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add Part' }));

  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(within(details).getByLabelText('Quantity for 309-127')).toHaveValue(
    '5',
  );
});

/* ============ New Work Order modal ============ */

test('＋ New Work Order opens a dialog over the Work Order list without changing the URL', async () => {
  await renderWorkOrders();

  const dialog = openNewWorkOrderDialog();

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(window.location.pathname).toBe('/management/work-orders');
  // The Work Order list stays mounted behind the overlay.
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
  expect(screen.getByText('007010')).toBeInTheDocument();
});

test('Cancel (Esc) closes a clean dialog and focus returns to ＋ New Work Order', async () => {
  await renderWorkOrders();
  const newWorkOrderButton = screen.getByRole('button', {
    name: '＋ New Work Order',
  });
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(newWorkOrderButton);
});

test('Escape and backdrop close a clean dialog', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  const dialog2 = openNewWorkOrderDialog();
  fireEvent.mouseDown(dialog2.parentElement!);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('closing a dirty New Work Order form requires confirmation and preserves entries', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007482' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // Nothing is silently discarded: an explicit confirmation appears.
  expect(
    screen.getByRole('dialog', { name: 'Discard this New Work Order?' }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/^WO Number/)).toHaveValue('007482');

  fireEvent.keyDown(screen.getByRole('dialog', { name: 'New Work Order' }), {
    key: 'Escape',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Discard entries' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a complete save flow (number + due entered) saves without extra confirmation', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007482' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });

  // Barcode scanning remains available as a secondary method.
  scanBarcode('PF:PN:78-04-0031');
  const qty = screen.getByLabelText('Quantity for 78-04-0031');
  expect(document.activeElement).toBe(qty);
  fireEvent.change(qty, { target: { value: '5' } });
  fireEvent.keyDown(qty, { key: 'Enter' });
  expect(document.activeElement).toBe(screen.getByLabelText('Scan PN barcode'));

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('007482')).toBeInTheDocument();
  expect(window.location.pathname).toBe('/management/work-orders');
  // The toast states the business rule only; per-surface persistence
  // explanations were consolidated into the single DevNotice (v16) and
  // rendered-copy.test.ts forbids the old wording everywhere.
  expect(
    screen.getByText(/007482 saved — business demand only/),
  ).toBeInTheDocument();
});

test('a non-PN barcode is rejected and adds nothing', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:MACHINE:L2');

  expect(
    screen.getByText(/Unknown barcode “PF:MACHINE:L2” — nothing added/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/No demand lines yet — add the first Part/),
  ).toBeInTheDocument();
});

test('a PN barcode carries the PN itself; an unknown PN is created on first use', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  // Arbitrary non-empty suffix — no format, no opaque id mapping.
  scanBarcode('PF:PN:NEW-PLATE-9');

  expect(screen.getByText('NEW-PLATE-9')).toBeInTheDocument();
  expect(
    screen.getByText(/new PN — barcode created with PN master/),
  ).toBeInTheDocument();

  // Case-insensitive identity: a re-scan in different casing focuses
  // the existing line instead of duplicating it.
  scanBarcode('PF:PN:new-plate-9');
  expect(screen.getAllByLabelText('Quantity for NEW-PLATE-9')).toHaveLength(1);
});

test('scanning a duplicate PN focuses the existing line instead of adding one', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:78-04-0031');
  scanBarcode('PF:PN:78-04-0031');

  const qtyFields = screen.getAllByLabelText('Quantity for 78-04-0031');
  expect(qtyFields).toHaveLength(1);
  expect(document.activeElement).toBe(qtyFields[0]);
});

test('search matches hyphenated PNs and WO numbers with punctuation', async () => {
  await renderWorkOrders();

  // Realistic PNs are multi-segment hyphenated strings; search must match
  // them literally (WO Number and PN preview).
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: '52-09-0114' },
  });
  expect(screen.getByText('007010')).toBeInTheDocument();
  expect(screen.queryByText('007005')).not.toBeInTheDocument();

  // An internal Work Order without an external number is found through
  // its PN preview and displays `—` as its number.
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: '214-406' },
  });
  expect(screen.queryByText('007010')).not.toBeInTheDocument();
  const internalRow = screen
    .getByText('internal Work Order — no external number yet')
    .closest('tr');
  expect(internalRow?.querySelector('.wo')?.textContent).toBe('—');
});

/* ============ Optional header + Save Demand confirmation ============ */

test('missing WO Number and due dates open a confirmation, never a validation error', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:78-04-0031');
  fireEvent.change(screen.getByLabelText('Quantity for 78-04-0031'), {
    target: { value: '5' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const confirm = screen.getByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  expect(confirm).toHaveTextContent(/No external WO Number/);
  expect(confirm).toHaveTextContent(
    /internal Work Order without an external number/,
  );
  expect(confirm).toHaveTextContent(/No WO due date/);
  expect(confirm).toHaveTextContent(/1 demand line has no due date/);
  // No temporary number is generated — the number stays NULL.
  expect(confirm.textContent).not.toMatch(/TMP-/);

  // Cancel returns to editing with every entered value preserved.
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep editing' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for 78-04-0031')).toHaveValue('5');

  // Confirming saves an internal Work Order with a NULL number that
  // renders as `—` (the placeholder itself is never persisted).
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and save' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const labels = screen.getAllByText(
    'internal Work Order — no external number yet',
  );
  expect(labels.length).toBeGreaterThan(0);
  const savedRow = labels[0].closest('tr');
  expect(savedRow?.querySelector('.wo')?.textContent).toBe('—');
  expect(screen.queryByText(/TMP-\d{8}/)).not.toBeInTheDocument();
});

test('an invalid quantity still blocks save and keeps entered values', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:78-04-0031');
  const qty = screen.getByLabelText('Quantity for 78-04-0031');
  fireEvent.change(qty, { target: { value: '0' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText('quantity must be a positive whole number'),
  ).toBeInTheDocument();
  expect(qty).toHaveValue('0'); // preserved, not cleared
  expect(document.activeElement).toBe(qty);
});

test('an existing WO Number is opened instead of duplicated', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007010' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // The New Work Order dialog closes and the existing Work Order opens
  // in the Work Order Details dialog instead.
  expect(
    screen.queryByRole('dialog', { name: 'New Work Order' }),
  ).not.toBeInTheDocument();
  const details = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  expect(details).toHaveTextContent('007010');
  expect(screen.getByText(/already exists/)).toBeInTheDocument();
});

/* ============ Multi-step Add Part dialog ============ */

test('the Add Part flow steps through PN, quantity, due date and metadata', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  const dialog = screen.getByRole('dialog', { name: /Add Part — step 1/ });

  // Step 1: search and select an existing PN.
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '309' },
  });
  fireEvent.click(screen.getByRole('button', { name: /309-127/ }));
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();

  // Step 2: same keypad + physical-keyboard interaction as Scan Station
  // (a real focusable numeric input, focused on entry).
  fireEvent.keyDown(dialog, { key: '5' });
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();

  // Back preserves entered values.
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Search PartNumber')).toHaveValue('309');
  fireEvent.click(screen.getByRole('button', { name: /309-127/ }));
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));

  // Step 3: `No due date` is an explicit, valid choice.
  fireEvent.click(screen.getByRole('radio', { name: /No due date/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));

  // Step 4: optional metadata, then finish.
  fireEvent.change(screen.getByLabelText('Job Numbers'), {
    target: { value: '18777' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add Part' }));

  // The flow created an editable draft row — nothing saved yet.
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for 309-127')).toHaveValue('5');
  const lineDue = screen.getByLabelText('Due date for 309-127');
  expect(lineDue).toHaveValue('');

  // The explicit `No due date` line never inherits a later WO due date…
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-15' },
  });
  expect(lineDue).toHaveValue('');
});

test('the Add Part flow rejects a PN already on the Work Order', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  scanBarcode('PF:PN:78-04-0031');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '78-04-0031' },
  });
  // Scope to the Add Part dialog: the scanned demand line behind it
  // exposes its own control whose accessible name carries the same PN.
  const addPartDialog = screen.getByRole('dialog', { name: /Add Part/ });
  fireEvent.click(
    within(addPartDialog).getByRole('button', { name: /78-04-0031/ }),
  );

  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByText(/78-04-0031 is already on this Work Order/),
  ).toBeInTheDocument();
  expect(screen.getAllByLabelText('Quantity for 78-04-0031')).toHaveLength(1);
});

/* ============ Dates ============ */

test('editable date fields are calendar inputs (type="date")', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  expect(screen.getByLabelText('Received date')).toHaveAttribute(
    'type',
    'date',
  );
  expect(screen.getByLabelText(/^WO due date/)).toHaveAttribute('type', 'date');

  scanBarcode('PF:PN:78-04-0031');
  expect(screen.getByLabelText('Due date for 78-04-0031')).toHaveAttribute(
    'type',
    'date',
  );
});

test('new lines inherit the WO due date; edited lines keep their own date', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:78-04-0031');
  scanBarcode('PF:PN:309-127');

  const due1 = screen.getByLabelText('Due date for 78-04-0031');
  const due2 = screen.getByLabelText('Due date for 309-127');
  expect(due1).toHaveValue('2026-09-01');
  expect(due2).toHaveValue('2026-09-01');

  // Manually edit one line, then change the WO due date.
  fireEvent.change(due1, { target: { value: '2026-09-10' } });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-15' },
  });

  expect(due1).toHaveValue('2026-09-10'); // manually edited — unchanged
  expect(due2).toHaveValue('2026-09-15'); // still inherited — follows
});

test('a line whose due date is cleared is user-edited and stops inheriting', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:78-04-0031');
  const due = screen.getByLabelText('Due date for 78-04-0031');
  fireEvent.change(due, { target: { value: '' } });
  expect(screen.getAllByText('No due date').length).toBeGreaterThan(0);

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-15' },
  });
  expect(due).toHaveValue(''); // explicit No due date — never overwritten
});

test('a Work Order without a due date displays cleanly in list and detail', async () => {
  await renderWorkOrders();

  // List: `—` (no due date) instead of an empty or invalid value.
  const row = screen.getByText('007011').closest('tr');
  expect(row?.textContent).toContain('—');

  const dialog = await openWorkOrderDetail('007011');
  const sub = dialog.querySelector('.wo-sub');
  expect(sub?.textContent).toContain('WO due date —');
});

/* ============ OPEN Work Order editing ============ */

test('an OPEN Work Order offers manual-first Add Part; a non-OPEN Work Order stays read-only', async () => {
  await renderWorkOrders();
  const open = await openWorkOrderDetail('007010');
  expect(
    within(open).getByRole('button', { name: '＋ Add Part manually' }),
  ).toBeEnabled();
  expect(within(open).getByLabelText('Scan PN barcode')).toBeInTheDocument();
  expect(
    within(open).getByRole('button', { name: 'Save demand' }),
  ).toBeInTheDocument();
  fireEvent.click(within(open).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  const released = await openWorkOrderDetail('007003');
  expect(
    within(released).queryByRole('button', { name: '＋ Add Part manually' }),
  ).not.toBeInTheDocument();
  expect(
    within(released).queryByRole('button', { name: /Remove line/ }),
  ).not.toBeInTheDocument();
  // Read-only: no Save demand, but the dialog still closes normally and
  // explains why editing is unavailable.
  expect(
    within(released).queryByRole('button', { name: 'Save demand' }),
  ).not.toBeInTheDocument();
  expect(released).toHaveTextContent(/demand lines are read-only/);
  expect(
    within(released).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeInTheDocument();
});

test('scanning a new PN on an OPEN Work Order adds a draft line and marks unsaved', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  scanBarcode('PF:PN:78-04-0031');

  expect(screen.getByText('78-04-0031')).toBeInTheDocument();
  // The new line joins the shipped invalid draft row as a second draft.
  expect(screen.getAllByText('Draft (unsaved)').length).toBeGreaterThanOrEqual(
    2,
  );
  expect(screen.getAllByText('● Unsaved changes').length).toBeGreaterThan(0);
});

test('a duplicate PN on an OPEN Work Order focuses the existing line', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  scanBarcode('PF:PN:0455-20-0118-03'); // already on WO 007010

  const qtyFields = screen.getAllByLabelText('Quantity for 0455-20-0118-03');
  expect(qtyFields).toHaveLength(1);
  expect(document.activeElement).toBe(qtyFields[0]);
});

test('an unsaved draft line is removed immediately without a confirmation dialog', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  scanBarcode('PF:PN:78-04-0031');
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line 78-04-0031' }),
  );

  // Only the Work Order Details dialog remains — no confirmation.
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(screen.queryByText('78-04-0031')).not.toBeInTheDocument();
});

test('removing a saved unreleased line requires confirmation', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line 52-09-0114' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Remove 52-09-0114 from 007010?',
  });
  expect(confirm).toBeInTheDocument();

  // Cancel keeps the line.
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep the line' }),
  );
  expect(screen.getByText('52-09-0114')).toBeInTheDocument();

  // Confirm removes it from the draft (mock state only).
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line 52-09-0114' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));
  expect(screen.queryByText('52-09-0114')).not.toBeInTheDocument();
  expect(screen.getByText(/removed from the draft/)).toBeInTheDocument();
});

test('a released line cannot be removed and explains why', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  const removeButton = screen.getByRole('button', {
    name: 'Remove line 2027-60-8114-00',
  });
  expect(removeButton).toBeDisabled();
  expect(removeButton).toHaveAttribute(
    'title',
    'Cannot remove: production quantity has already been released.',
  );
  expect(
    screen.getAllByText(
      'Cannot remove: production quantity has already been released.',
    ).length,
  ).toBeGreaterThan(0);
});

test('saving an edited OPEN Work Order reports demand-only saving', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  // The mock data ships one invalid draft row — remove it first (drafts
  // are removed immediately), then save a real edit.
  fireEvent.click(screen.getByRole('button', { name: 'Remove draft line' }));
  fireEvent.change(screen.getByLabelText('Quantity for 0455-20-0118-03'), {
    target: { value: '14' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // The toast states the business rule only; the mock/persistence
  // boundary lives solely in the shared DevNotice (v16).
  expect(
    screen.getByText(/demand updated — business demand only/),
  ).toBeInTheDocument();
  // Saving keeps Work Order Details open with the draft now clean.
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(screen.queryByText('● Unsaved changes')).not.toBeInTheDocument();
});

test('clearing the WO due date on an OPEN Work Order asks for explicit confirmation', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  fireEvent.click(screen.getByRole('button', { name: 'Remove draft line' }));
  fireEvent.change(screen.getByLabelText('WO due date'), {
    target: { value: '' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const confirm = screen.getByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  expect(confirm).toHaveTextContent(/No WO due date/);

  // Cancel returns to editing — nothing saved.
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep editing' }),
  );
  expect(
    screen.queryByText(/demand updated — business demand only/),
  ).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and save' }));
  expect(
    screen.getByText(/demand updated — business demand only/),
  ).toBeInTheDocument();
});

test('saving an OPEN Work Order with an incomplete row is blocked, not filtered', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  // WO 007010 ships with an invalid row (no PN, qty 0).
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(
    screen.getByText('PN is required — look up or create the PartNumber'),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/demand updated — business demand only/),
  ).not.toBeInTheDocument();
});

/* ============ Unsaved changes and navigation protection ============ */

async function makeDetailDirty() {
  const dialog = await openWorkOrderDetail('007010');
  fireEvent.change(
    within(dialog).getByLabelText('Quantity for 0455-20-0118-03'),
    {
      target: { value: '99' },
    },
  );
  expect(screen.getAllByText('● Unsaved changes').length).toBeGreaterThan(0);
  return dialog;
}

test('cancelling the discard confirmation keeps the user on the dirty Work Order', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('confirming the discard allows top-level navigation away', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  vi.spyOn(window, 'confirm').mockReturnValue(true);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(window.location.pathname).toBe('/scan-station');
});

test('Management sub-navigation is guarded too', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'PN Tracking' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
});

test('browser back is guarded while the Work Order detail is dirty', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  // Simulate the browser's back/forward navigation event.
  fireEvent.popState(window);

  expect(confirmSpy).toHaveBeenCalled();
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('reload and tab close are guarded through beforeunload while dirty', async () => {
  await renderWorkOrders();
  await makeDetailDirty();

  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);

  // Saving clears the protection (the shipped invalid row is removed
  // first so the save is valid).
  fireEvent.click(screen.getByRole('button', { name: 'Remove draft line' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  const after = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(after);
  expect(after.defaultPrevented).toBe(false);
});

test('a dirty New Work Order dialog also guards top-level navigation', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007490' },
  });
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
});
