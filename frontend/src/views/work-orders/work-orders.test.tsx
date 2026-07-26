import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Work Orders regression tests: New Work Order modal workflow (optional
// WO Number and due date, temporary internal number generation,
// missing-information Save Demand confirmation), the multi-step Add
// Part dialog, OPEN Work Order editing, nullable due-date behavior,
// mock validation, and unsaved-change protection. Everything here
// exercises Phase 2 development mock state only.

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

async function openWorkOrderDetail(workOrderNumber: string) {
  fireEvent.click(
    screen.getByRole('button', { name: new RegExp(workOrderNumber) }),
  );
  await screen.findByRole('heading', { name: workOrderNumber });
}

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

test('Cancel closes a clean dialog and focus returns to ＋ New Work Order', async () => {
  await renderWorkOrders();
  const newWorkOrderButton = screen.getByRole('button', {
    name: '＋ New Work Order',
  });
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

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
  scanBarcode('PF:PN:1021');
  const qty = screen.getByLabelText('Quantity for 78-04-0031');
  expect(document.activeElement).toBe(qty);
  fireEvent.change(qty, { target: { value: '5' } });
  fireEvent.keyDown(qty, { key: 'Enter' });
  expect(document.activeElement).toBe(screen.getByLabelText('Scan PN barcode'));

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('007482')).toBeInTheDocument();
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(screen.getByText(/007482 saved \(mock\)/)).toBeInTheDocument();
  expect(
    screen.getByText(/Nothing was persisted to the backend/),
  ).toBeInTheDocument();
});

test('an unknown barcode is rejected and adds nothing', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:9999');

  expect(
    screen.getByText(/Unknown barcode “PF:PN:9999” — nothing added/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/No demand lines yet — add the first Part/),
  ).toBeInTheDocument();
});

test('scanning a duplicate PN focuses the existing line instead of adding one', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:1021');
  scanBarcode('PF:PN:1021');

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

  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'TMP-20260721' },
  });
  expect(screen.getByText('TMP-20260721-0940-REWORK')).toBeInTheDocument();
  expect(screen.queryByText('007010')).not.toBeInTheDocument();
});

/* ============ Optional header + Save Demand confirmation ============ */

test('missing WO Number and due dates open a confirmation, never a validation error', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:1021');
  fireEvent.change(screen.getByLabelText('Quantity for 78-04-0031'), {
    target: { value: '5' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const confirm = screen.getByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  expect(confirm).toHaveTextContent(/No external WO Number/);
  expect(confirm).toHaveTextContent(/TMP-YYYYMMDD-HHMMSS/);
  expect(confirm).toHaveTextContent(/No WO due date/);
  expect(confirm).toHaveTextContent(/1 demand line has no due date/);

  // Cancel returns to editing with every entered value preserved.
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep editing' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for 78-04-0031')).toHaveValue('5');

  // Confirming generates the labeled temporary internal WO Number.
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and save' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/^TMP-\d{8}-\d{6}$/)).toBeInTheDocument();
  expect(
    screen.getAllByText('temporary internal Work Order').length,
  ).toBeGreaterThan(0);
});

test('an invalid quantity still blocks save and keeps entered values', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:1021');
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

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: '007010' }));
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

  // Step 2: same keypad + physical-keyboard interaction as Scan Station.
  fireEvent.keyDown(dialog, { key: '5' });
  expect(
    screen.getByRole('status', { name: 'Quantity: 5' }),
  ).toBeInTheDocument();

  // Back preserves entered values.
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Search PartNumber')).toHaveValue('309');
  fireEvent.click(screen.getByRole('button', { name: /309-127/ }));
  expect(
    screen.getByRole('status', { name: 'Quantity: 5' }),
  ).toBeInTheDocument();
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
  scanBarcode('PF:PN:1021');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '78-04-0031' },
  });
  fireEvent.click(screen.getByRole('button', { name: /78-04-0031/ }));

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

  scanBarcode('PF:PN:1021');
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
  scanBarcode('PF:PN:1021');
  scanBarcode('PF:PN:1102');

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
  scanBarcode('PF:PN:1021');
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

  await openWorkOrderDetail('007011');
  const sub = document.querySelector('.wo-sub');
  expect(sub?.textContent).toContain('WO due date —');
});

/* ============ OPEN Work Order editing ============ */

test('an OPEN Work Order offers manual-first Add Part; a non-OPEN Work Order stays read-only', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');
  expect(
    screen.getByRole('button', { name: '＋ Add Part manually' }),
  ).toBeEnabled();
  expect(screen.getByLabelText('Scan PN barcode')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '‹ All Work Orders' }));
  await openWorkOrderDetail('007003');
  expect(
    screen.queryByRole('button', { name: '＋ Add Part manually' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Remove line/ }),
  ).not.toBeInTheDocument();
});

test('scanning a new PN on an OPEN Work Order adds a draft line and marks unsaved', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  scanBarcode('PF:PN:1021');

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

  scanBarcode('PF:PN:1014'); // 0455-20-0118-03 is already on WO 007010

  const qtyFields = screen.getAllByLabelText('Quantity for 0455-20-0118-03');
  expect(qtyFields).toHaveLength(1);
  expect(document.activeElement).toBe(qtyFields[0]);
});

test('an unsaved draft line is removed immediately without a dialog', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  scanBarcode('PF:PN:1021');
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line 78-04-0031' }),
  );

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
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
  expect(screen.getByText(/local mock state only/)).toBeInTheDocument();
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

test('saving an edited OPEN Work Order updates local mock state and reports mock-only', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  // The mock data ships one invalid draft row — remove it first (drafts
  // are removed immediately), then save a real edit.
  fireEvent.click(screen.getByRole('button', { name: 'Remove draft line' }));
  fireEvent.change(screen.getByLabelText('Quantity for 0455-20-0118-03'), {
    target: { value: '14' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.getByText(/demand updated \(mock\)/)).toBeInTheDocument();
  expect(
    screen.getByText(/nothing was persisted to the backend/),
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
  expect(screen.queryByText(/demand updated \(mock\)/)).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and save' }));
  expect(screen.getByText(/demand updated \(mock\)/)).toBeInTheDocument();
});

test('saving an OPEN Work Order with an incomplete row is blocked, not filtered', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007010');

  // WO 007010 ships with an invalid row (no PN, qty 0).
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(
    screen.getByText('PN is required — look up or create the PartNumber'),
  ).toBeInTheDocument();
  expect(screen.queryByText(/demand updated \(mock\)/)).not.toBeInTheDocument();
});

/* ============ Unsaved changes and navigation protection ============ */

async function makeDetailDirty() {
  await openWorkOrderDetail('007010');
  fireEvent.change(screen.getByLabelText('Quantity for 0455-20-0118-03'), {
    target: { value: '99' },
  });
  expect(screen.getAllByText('● Unsaved changes').length).toBeGreaterThan(0);
}

test('cancelling the discard confirmation keeps the user on the dirty Work Order', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(screen.getByRole('heading', { name: '007010' })).toBeInTheDocument();
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

  fireEvent.click(screen.getByRole('link', { name: 'Tracking' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
});

test('returning to the Work Order list from a dirty detail asks for confirmation', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('button', { name: '‹ All Work Orders' }));
  expect(confirmSpy).toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: '007010' })).toBeInTheDocument();

  confirmSpy.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: '‹ All Work Orders' }));
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
});

test('browser back is guarded while the Work Order detail is dirty', async () => {
  await renderWorkOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  // Simulate the browser's back/forward navigation event.
  fireEvent.popState(window);

  expect(confirmSpy).toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: '007010' })).toBeInTheDocument();
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
