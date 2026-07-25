import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Purchase Orders regression tests: New PO modal workflow, OPEN PO
// editing (add / remove demand lines), calendar date behavior, mock
// validation, and unsaved-change protection. Everything here exercises
// Phase 2 development mock state only.

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

async function renderPurchaseOrders() {
  window.history.replaceState({}, '', '/management/purchase-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Purchase Orders' });
}

function openNewPoDialog() {
  const button = screen.getByRole('button', { name: '＋ New PO' });
  // A real click focuses the button first; jsdom needs this explicitly
  // so focus restoration on close can be asserted.
  button.focus();
  fireEvent.click(button);
  return screen.getByRole('dialog', { name: 'New PO' });
}

function scanBarcode(barcode: string) {
  const scan = screen.getByLabelText('Scan PN barcode');
  fireEvent.change(scan, { target: { value: barcode } });
  fireEvent.keyDown(scan, { key: 'Enter' });
  return scan;
}

async function openPoDetail(po: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(po) }));
  await screen.findByRole('heading', { name: po });
}

/* ============ New PO modal ============ */

test('＋ New PO opens a dialog over the PO list without changing the URL', async () => {
  await renderPurchaseOrders();

  const dialog = openNewPoDialog();

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(window.location.pathname).toBe('/management/purchase-orders');
  // The PO list stays mounted behind the overlay.
  expect(
    screen.getByRole('heading', { name: 'Purchase Orders' }),
  ).toBeInTheDocument();
  expect(screen.getByText('PO-1010')).toBeInTheDocument();
});

test('Cancel closes a clean dialog and focus returns to ＋ New PO', async () => {
  await renderPurchaseOrders();
  const newPoButton = screen.getByRole('button', { name: '＋ New PO' });
  openNewPoDialog();

  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(newPoButton);
});

test('Escape and backdrop close a clean dialog', async () => {
  await renderPurchaseOrders();
  const dialog = openNewPoDialog();

  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  const dialog2 = openNewPoDialog();
  fireEvent.mouseDown(dialog2.parentElement!);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('closing a dirty New PO form requires confirmation and preserves entries', async () => {
  await renderPurchaseOrders();
  const dialog = openNewPoDialog();

  fireEvent.change(screen.getByLabelText('PO Number'), {
    target: { value: 'PO-2001' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // Nothing is silently discarded: an explicit confirmation appears.
  expect(
    screen.getByRole('dialog', { name: 'Discard this New PO?' }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(screen.getByRole('dialog', { name: 'New PO' })).toBeInTheDocument();
  expect(screen.getByLabelText('PO Number')).toHaveValue('PO-2001');

  fireEvent.keyDown(screen.getByRole('dialog', { name: 'New PO' }), {
    key: 'Escape',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Discard entries' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('the scanner-first save flow adds the PO to the list and reports mock-only', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  fireEvent.change(screen.getByLabelText('PO Number'), {
    target: { value: 'PO-2001' },
  });
  fireEvent.change(screen.getByLabelText('PO due date'), {
    target: { value: '2026-09-01' },
  });

  scanBarcode('PF:PN:1021');
  // Scanning appends a line and focuses its quantity field.
  const qty = screen.getByLabelText('Quantity for PF-HOUSING-021');
  expect(document.activeElement).toBe(qty);
  fireEvent.change(qty, { target: { value: '5' } });
  // Enter returns focus to the scan input, ready for the next part.
  fireEvent.keyDown(qty, { key: 'Enter' });
  expect(document.activeElement).toBe(screen.getByLabelText('Scan PN barcode'));

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('PO-2001')).toBeInTheDocument();
  expect(window.location.pathname).toBe('/management/purchase-orders');
  expect(screen.getByText(/PO-2001 saved \(mock\)/)).toBeInTheDocument();
  expect(
    screen.getByText(/Nothing was persisted to the backend/),
  ).toBeInTheDocument();
});

test('an unknown barcode is rejected and adds nothing', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  scanBarcode('PF:PN:9999');

  expect(
    screen.getByText(/Unknown barcode “PF:PN:9999” — nothing added/),
  ).toBeInTheDocument();
  expect(
    screen.getByText('No demand lines yet — scan the first PN barcode above'),
  ).toBeInTheDocument();
});

test('scanning a duplicate PN focuses the existing line instead of adding one', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  scanBarcode('PF:PN:1021');
  scanBarcode('PF:PN:1021');

  const qtyFields = screen.getAllByLabelText('Quantity for PF-HOUSING-021');
  expect(qtyFields).toHaveLength(1);
  expect(document.activeElement).toBe(qtyFields[0]);
});

/* ============ Validation ============ */

test('a manual row with a blank PN blocks save and is not silently dropped', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  fireEvent.change(screen.getByLabelText('PO Number'), {
    target: { value: 'PO-2002' },
  });
  fireEvent.change(screen.getByLabelText('PO due date'), {
    target: { value: '2026-09-01' },
  });
  fireEvent.click(screen.getByRole('button', { name: '＋ Add line manually' }));

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // The dialog stays open, the row stays, and the PN error is shown.
  expect(screen.getByRole('dialog', { name: 'New PO' })).toBeInTheDocument();
  expect(
    screen.getByText('PN is required — look up or create the PartNumber'),
  ).toBeInTheDocument();
  const pnInput = screen.getByLabelText('PartNumber lookup or create');
  expect(document.activeElement).toBe(pnInput);
});

test('an invalid quantity blocks save and keeps entered values', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  fireEvent.change(screen.getByLabelText('PO Number'), {
    target: { value: 'PO-2003' },
  });
  fireEvent.change(screen.getByLabelText('PO due date'), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:1021');
  const qty = screen.getByLabelText('Quantity for PF-HOUSING-021');
  fireEvent.change(qty, { target: { value: '0' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.getByRole('dialog', { name: 'New PO' })).toBeInTheDocument();
  expect(
    screen.getByText('quantity must be a positive whole number'),
  ).toBeInTheDocument();
  expect(qty).toHaveValue('0'); // preserved, not cleared
  expect(document.activeElement).toBe(qty);
});

test('a missing PO Number blocks save and focuses the field', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.getByText('PO Number is required')).toBeInTheDocument();
  expect(document.activeElement).toBe(screen.getByLabelText('PO Number'));
});

test('an existing PO Number is opened instead of duplicated', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  fireEvent.change(screen.getByLabelText('PO Number'), {
    target: { value: 'PO-1010' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: 'PO-1010' }));
  expect(screen.getByText(/already exists/)).toBeInTheDocument();
});

/* ============ Dates ============ */

test('editable date fields are calendar inputs (type="date")', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  expect(screen.getByLabelText('Received date')).toHaveAttribute(
    'type',
    'date',
  );
  expect(screen.getByLabelText('PO due date')).toHaveAttribute('type', 'date');

  scanBarcode('PF:PN:1021');
  expect(screen.getByLabelText('Due date for PF-HOUSING-021')).toHaveAttribute(
    'type',
    'date',
  );
});

test('new lines inherit the PO due date; edited lines keep their own date', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();

  fireEvent.change(screen.getByLabelText('PO due date'), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:1021');
  scanBarcode('PF:PN:1102');

  const due1 = screen.getByLabelText('Due date for PF-HOUSING-021');
  const due2 = screen.getByLabelText('Due date for PF-PIN-102');
  expect(due1).toHaveValue('2026-09-01');
  expect(due2).toHaveValue('2026-09-01');

  // Manually edit one line, then change the PO due date.
  fireEvent.change(due1, { target: { value: '2026-09-10' } });
  fireEvent.change(screen.getByLabelText('PO due date'), {
    target: { value: '2026-09-15' },
  });

  expect(due1).toHaveValue('2026-09-10'); // manually edited — unchanged
  expect(due2).toHaveValue('2026-09-15'); // still inherited — follows
});

/* ============ OPEN PO editing ============ */

test('an OPEN PO offers ＋ Add Part; a non-OPEN PO stays read-only', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');
  expect(screen.getByRole('button', { name: '＋ Add Part' })).toBeEnabled();

  fireEvent.click(screen.getByRole('button', { name: '‹ All POs' }));
  await openPoDetail('PO-1003');
  expect(
    screen.queryByRole('button', { name: '＋ Add Part' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Remove line/ }),
  ).not.toBeInTheDocument();
});

test('scanning a new PN on an OPEN PO adds a draft line and marks unsaved', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part' }));
  scanBarcode('PF:PN:1021');

  expect(screen.getByText('PF-HOUSING-021')).toBeInTheDocument();
  // The new line joins the shipped invalid draft row as a second draft.
  expect(screen.getAllByText('Draft (unsaved)').length).toBeGreaterThanOrEqual(
    2,
  );
  expect(screen.getAllByText('● Unsaved changes').length).toBeGreaterThan(0);
});

test('a duplicate PN on an OPEN PO focuses the existing line', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part' }));
  scanBarcode('PF:PN:1014'); // PF-SHAFT-014 is already on PO-1010

  const qtyFields = screen.getAllByLabelText('Quantity for PF-SHAFT-014');
  expect(qtyFields).toHaveLength(1);
  expect(document.activeElement).toBe(qtyFields[0]);
});

test('an unsaved draft line is removed immediately without a dialog', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part' }));
  scanBarcode('PF:PN:1021');
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line PF-HOUSING-021' }),
  );

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('PF-HOUSING-021')).not.toBeInTheDocument();
});

test('removing a saved unreleased line requires confirmation', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line PF-GEAR-201' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Remove PF-GEAR-201 from PO-1010?',
  });
  expect(confirm).toBeInTheDocument();

  // Cancel keeps the line.
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep the line' }),
  );
  expect(screen.getByText('PF-GEAR-201')).toBeInTheDocument();

  // Confirm removes it from the draft (mock state only).
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove line PF-GEAR-201' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));
  expect(screen.queryByText('PF-GEAR-201')).not.toBeInTheDocument();
  expect(screen.getByText(/local mock state only/)).toBeInTheDocument();
});

test('a released line cannot be removed and explains why', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  const removeButton = screen.getByRole('button', {
    name: 'Remove line PF-BRACKET-001',
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

test('saving an edited OPEN PO updates local mock state and reports mock-only', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  // The mock data ships one invalid draft row — remove it first (drafts
  // are removed immediately), then save a real edit.
  fireEvent.click(screen.getByRole('button', { name: 'Remove draft line' }));
  fireEvent.change(screen.getByLabelText('Quantity for PF-SHAFT-014'), {
    target: { value: '14' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(screen.getByText(/demand updated \(mock\)/)).toBeInTheDocument();
  expect(
    screen.getByText(/nothing was persisted to the backend/),
  ).toBeInTheDocument();
  expect(screen.queryByText('● Unsaved changes')).not.toBeInTheDocument();
});

test('saving an OPEN PO with an incomplete row is blocked, not filtered', async () => {
  await renderPurchaseOrders();
  await openPoDetail('PO-1010');

  // PO-1010 ships with an invalid row (no PN, qty 0, no due date).
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(
    screen.getByText('PN is required — look up or create the PartNumber'),
  ).toBeInTheDocument();
  expect(screen.queryByText(/demand updated \(mock\)/)).not.toBeInTheDocument();
});

/* ============ Unsaved changes and navigation protection ============ */

async function makeDetailDirty() {
  await openPoDetail('PO-1010');
  fireEvent.change(screen.getByLabelText('Quantity for PF-SHAFT-014'), {
    target: { value: '99' },
  });
  expect(screen.getAllByText('● Unsaved changes').length).toBeGreaterThan(0);
}

test('cancelling the discard confirmation keeps the user on the dirty PO', async () => {
  await renderPurchaseOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/purchase-orders');
  expect(screen.getByRole('heading', { name: 'PO-1010' })).toBeInTheDocument();
});

test('confirming the discard allows top-level navigation away', async () => {
  await renderPurchaseOrders();
  await makeDetailDirty();
  vi.spyOn(window, 'confirm').mockReturnValue(true);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(window.location.pathname).toBe('/scan-station');
});

test('Management sub-navigation is guarded too', async () => {
  await renderPurchaseOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Tracking' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/purchase-orders');
});

test('returning to the PO list from a dirty detail asks for confirmation', async () => {
  await renderPurchaseOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('button', { name: '‹ All POs' }));
  expect(confirmSpy).toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: 'PO-1010' })).toBeInTheDocument();

  confirmSpy.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: '‹ All POs' }));
  expect(
    screen.getByRole('heading', { name: 'Purchase Orders' }),
  ).toBeInTheDocument();
});

test('browser back is guarded while the PO detail is dirty', async () => {
  await renderPurchaseOrders();
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  // Simulate the browser's back/forward navigation event.
  fireEvent.popState(window);

  expect(confirmSpy).toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: 'PO-1010' })).toBeInTheDocument();
});

test('reload and tab close are guarded through beforeunload while dirty', async () => {
  await renderPurchaseOrders();
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

test('a dirty New PO dialog also guards top-level navigation', async () => {
  await renderPurchaseOrders();
  openNewPoDialog();
  fireEvent.change(screen.getByLabelText('PO Number'), {
    target: { value: 'PO-2010' },
  });
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/purchase-orders');
  expect(screen.getByRole('dialog', { name: 'New PO' })).toBeInTheDocument();
});
