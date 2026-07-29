import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Scan Station regressions for the PN-centric one-shot redesign:
// station selection routing, no Machine Session, Machine-first and
// PN-first one-shot dialogs, explicit quantity-source selection,
// auditable quantity addition / repair / scrap, Undo confirmation, the
// shared Area/Machine monitoring layout, and quantity-keypad keyboard
// behavior.

let failing = false;

beforeEach(() => {
  failing = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      failing
        ? Promise.reject(new TypeError('Failed to fetch'))
        : Promise.resolve(
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

async function renderStation(stationId = 'LATHE-ST-01') {
  window.history.replaceState({}, '', `/scan-station/${stationId}`);
  render(<App />);
  return await screen.findByLabelText('Scan barcode');
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function activeDialog() {
  return screen.getByRole('dialog');
}

/* ============ Routing and station identity ============ */

test('/scan-station shows the Station Selector without auto-redirecting', async () => {
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);

  expect(
    await screen.findByRole('heading', { name: 'Select a Scan Station' }),
  ).toBeInTheDocument();
  expect(window.location.pathname).toBe('/scan-station');
  // Enough information to distinguish stations: id, Area, Machine mode.
  const lathe = screen.getByRole('button', { name: /LATHE-ST-01/ });
  expect(lathe).toHaveTextContent('Machine Shop');
  expect(lathe).toHaveTextContent('4 Machines — queue & assign');
  const deburr = screen.getByRole('button', { name: /DEBURR-ST-01/ });
  expect(deburr).toHaveTextContent('No Machines — direct Area processing');
});

test('an unknown Station ID is an explicit error, never a fallback', async () => {
  window.history.replaceState({}, '', '/scan-station/GHOST-ST-99');
  render(<App />);

  expect(
    await screen.findByText(/Unknown or inactive Scan Station “GHOST-ST-99”/),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Scan barcode')).not.toBeInTheDocument();
});

test('the footer Station ID is a subtle switch back to the selector', async () => {
  await renderStation();

  const switchButton = screen.getByRole('button', {
    name: 'LATHE-ST-01',
  });
  expect(switchButton.closest('.ss-stationfoot')).not.toBeNull();
  fireEvent.click(switchButton);

  expect(window.location.pathname).toBe('/scan-station');
  expect(
    await screen.findByRole('heading', { name: 'Select a Scan Station' }),
  ).toBeInTheDocument();
});

/* ============ Area with / without Machines ============ */

test('an Area with Machines shows queue statistics and the shared layout', async () => {
  await renderStation();

  const stats = document.querySelector('.ss-stats');
  expect(stats?.textContent).toContain('Queued');
  expect(stats?.textContent).toContain('On machines');
  // Shared layout: left summary card + right-side Machine cards grid.
  const layout = document.querySelector('.am');
  expect(layout).not.toBeNull();
  expect(layout?.classList.contains('am-single')).toBe(false);
  const machineCards = document.querySelectorAll('.am-machines .abd-machine');
  expect(machineCards.length).toBe(4);
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('Assigned to Machines');
  expect(summary?.textContent).toContain('Area queue — awaiting Machine');
});

test('an Area without Machines renders a full-width card and no Machine region', async () => {
  await renderStation('DEBURR-ST-01');

  // No meaningless queue/Machine statistics.
  const stats = document.querySelector('.ss-stats');
  expect(stats?.textContent).toContain('Processing');
  expect(stats?.textContent).not.toContain('Queued');
  expect(stats?.textContent).not.toContain('On machines');
  // Full-width single-column layout, no Machine cards at all.
  const layout = document.querySelector('.am');
  expect(layout?.classList.contains('am-single')).toBe(true);
  expect(document.querySelector('.abd-machine')).toBeNull();
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('In processing');
  expect(summary?.textContent).not.toContain('awaiting Machine');
});

/* ============ No Machine Session — one-shot Machine scan ============ */

test('a Machine scan opens a one-shot assignment dialog and leaves nothing armed', async () => {
  await renderStation();

  scan('PF:MACHINE:L2');
  const dialog = await screen.findByRole('dialog', {
    name: 'One-shot Machine assignment',
  });
  expect(dialog).toHaveTextContent('no persistent Machine Session');

  // Cancel: no write, no sticky Machine context anywhere.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(document.querySelector('.ss-head')?.textContent).not.toContain(
    'Lathe 2',
  );
});

test('the Machine-first flow completes: PN pick → MAX quantity → summary → confirm', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  // Machine preselected from the scan.
  expect(
    within(dialog as HTMLElement).getByRole('button', { name: /Lathe 1/ }),
  ).toHaveClass('sel');

  // Pick a queued PN — quantity defaults to MAX (queued 2).
  fireEvent.click(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();
  expect(dialog.textContent).toContain('Summary: ASSIGNED_TO_MACHINE');

  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/2027-60-8114-00 × 2 → Lathe 1/)).toBeInTheDocument();
  // The completed action becomes the Last Scanned PN.
  expect(document.querySelector('.ss-lastpn')?.textContent).toContain(
    '2027-60-8114-00',
  );
});

test('an inactive Machine is rejected with no write', async () => {
  await renderStation();

  scan('PF:MACHINE:L4');
  expect(
    screen.getByText('Lathe 4 is inactive (maintenance)'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

/* ============ PN scan resolution ============ */

test('a PN with quantity in the Area opens the one-shot action dialog with valid choices only', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  const dialog = await screen.findByRole('dialog', {
    name: 'Choose the action for this PN',
  });
  // Queued quantity exists → assign choice; quantity at Cut → receive.
  expect(dialog).toHaveTextContent('Assign queued quantity to a Machine');
  expect(dialog).toHaveTextContent('Receive more quantity from another Area');
  expect(dialog).toHaveTextContent('Add more quantity');
  expect(dialog).toHaveTextContent('Scrap damaged quantity');
  // No repair source exists for this PN → the intent is not offered.
  expect(dialog).not.toHaveTextContent('Send quantity here for repair');

  // Cancel abandons with no write and never touches Last Scanned PN.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe('—');
});

test('a PN elsewhere with one source goes straight to quantity entry with MAX default', async () => {
  await renderStation();

  scan('PF:PN:118-052'); // only at Manual (4 pcs)
  const dialog = await screen.findByRole('dialog', { name: 'Enter quantity' });
  expect(dialog).toHaveTextContent('Transfer Manual → Lathe');
  // MAX defaults to the available source quantity.
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();

  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/118-052 × 4 → Lathe queue/)).toBeInTheDocument();
});

test('multiple sources require explicit selection — never combined silently', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031'); // Mill 3 + Deburr 3
  const dialog = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  expect(dialog).toHaveTextContent('never combined silently');
  expect(dialog).toHaveTextContent('Mill — 3 pcs available');
  expect(dialog).toHaveTextContent('Deburr — 3 pcs available');

  fireEvent.click(screen.getByRole('button', { name: /Deburr — 3 pcs/ }));
  const qtyDialog = await screen.findByRole('dialog', {
    name: 'Enter quantity',
  });
  expect(qtyDialog).toHaveTextContent('Transfer Deburr → Lathe');
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
});

/* ============ Intake (no active WO Demand) ============ */

test('an unknown PN opens the intake flow with editable MODIFY + FLOATING defaults', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive quantity — intake',
  });
  expect(dialog).toHaveTextContent('created on first valid use');
  // Defaults are editable selections, not forced values.
  expect(screen.getByLabelText('Request Type')).toHaveValue('MODIFY');
  expect(screen.getByLabelText('Route Mode')).toHaveValue('FLOATING');
  fireEvent.change(screen.getByLabelText('Request Type'), {
    target: { value: 'NEW' },
  });
  expect(screen.getByLabelText('Request Type')).toHaveValue('NEW');
  fireEvent.change(screen.getByLabelText('Request Type'), {
    target: { value: 'MODIFY' },
  });

  fireEvent.keyDown(dialog, { key: '6' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm intake' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByText(/NEW-PART-01 × 6 received into Lathe queue/),
  ).toBeInTheDocument();
  // Internal Work Order without an external number — no TMP number.
  const detail = document.querySelector('.ss-feedback .t2')?.textContent ?? '';
  expect(detail).toContain('without an external number (displays —)');
  expect(detail).not.toMatch(/TMP-/);
});

test('PN identity is case-insensitive and keeps the first-entered casing', async () => {
  await renderStation();

  scan('PF:PN:abc-part');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive quantity — intake',
  });
  fireEvent.keyDown(dialog, { key: '1' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm intake' }));
  expect(screen.getByText(/abc-part × 1/)).toBeInTheDocument();

  // Scanning the same PN in a different casing resolves to the SAME
  // PartNumber and shows the preserved original casing.
  scan('PF:PN:ABC-PART');
  const dialog2 = await screen.findByRole('dialog', {
    name: 'Receive quantity — intake',
  });
  expect(within(dialog2 as HTMLElement).getByText('abc-part')).toBeVisible();
  expect(
    within(dialog2 as HTMLElement).queryByText('ABC-PART'),
  ).not.toBeInTheDocument();
});

/* ============ Add more quantity ============ */

test('Add more quantity requires a reason, has no MAX and no default', async () => {
  await renderStation();

  scan('PF:PN:0455-20-0118-03');
  fireEvent.click(screen.getByRole('button', { name: /Add more quantity/ }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Add more quantity',
  });

  // No default value and no MAX shortcut.
  expect(screen.getByLabelText('Quantity: none')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^MAX/ })).toBeNull();

  fireEvent.keyDown(dialog, { key: '2' });
  const confirm = screen.getByRole('button', { name: 'Confirm addition' });
  expect(confirm).toBeDisabled(); // reason still missing

  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: 'found 2 extra blanks' },
  });
  expect(confirm).toBeEnabled();
  expect(dialog).toHaveTextContent('QUANTITY_ADJUSTED (INCREASE)');
  fireEvent.click(confirm);

  expect(
    screen.getByText(/0455-20-0118-03 \+2 pcs at Lathe/),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-feedback .t2')?.textContent).toContain(
    'never changes the WO Demand requested quantity',
  );
});

/* ============ Repair ============ */

test('the Repair intent is explicit: source, quantity, reason and a Repair summary', async () => {
  await renderStation();

  scan('PF:PN:0455-20-0118-03');
  const actions = await screen.findByRole('dialog', {
    name: 'Choose the action for this PN',
  });
  // A repair source exists for this PN → the intent is offered, but
  // never auto-selected.
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', {
      name: /Send quantity here for repair/,
    }),
  );

  const dialog = await screen.findByRole('dialog', {
    name: 'Send quantity here for repair',
  });
  expect(dialog).toHaveTextContent('movement_reason REPAIR');
  expect(dialog).toHaveTextContent('never a Request Type');

  // The single known source is preselected; quantity defaults to MAX.
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();
  const confirm = screen.getByRole('button', {
    name: 'Confirm repair movement',
  });
  expect(confirm).toBeDisabled(); // reason is required

  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: 'shoulder cut short' },
  });
  expect(confirm).toBeEnabled();
  // The summary identifies the movement as a Repair movement.
  expect(dialog.textContent).toContain('Summary: REPAIR movement');
  fireEvent.click(confirm);

  expect(
    screen.getByText(/REPAIR — 0455-20-0118-03 × 4 returns to Lathe/),
  ).toBeInTheDocument();
});

/* ============ Scrap ============ */

test('the Scrap workflow counts PF:SCRAP scans, requires a reason, and cancel discards', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  fireEvent.click(
    screen.getByRole('button', { name: /Scrap damaged quantity/ }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Scrap damaged quantity',
  });
  const scrapInput = screen.getByLabelText('Scrap barcode input');

  // Each PF:SCRAP scan increments the pending counter by one; other
  // values are rejected inside the workflow.
  for (let i = 0; i < 3; i += 1) {
    fireEvent.change(scrapInput, { target: { value: 'PF:SCRAP' } });
    fireEvent.keyDown(scrapInput, { key: 'Enter' });
  }
  fireEvent.change(scrapInput, { target: { value: 'PF:PN:1' } });
  fireEvent.keyDown(scrapInput, { key: 'Enter' });
  expect(screen.getByText(/only PF:SCRAP counts here/)).toBeInTheDocument();
  expect(dialog.querySelector('.ss-scrapcount .cnt')?.textContent).toBe('3');

  // The count can be corrected before confirmation.
  fireEvent.click(screen.getByRole('button', { name: '−1 correct' }));
  expect(dialog.querySelector('.ss-scrapcount .cnt')?.textContent).toBe('2');

  // Confirm stays blocked until the common reason exists.
  const confirm = screen.getByRole('button', { name: 'Confirm scrap' });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Common scrap reason (required)'), {
    target: { value: 'tool crash' },
  });
  expect(confirm).toBeEnabled();

  // The summary carries PN, Area, available, scrap, remaining, reason.
  expect(dialog.textContent).toContain('available 6');
  expect(dialog.textContent).toContain('scrap 2');
  expect(dialog.textContent).toContain('remaining 4');

  // Cancel discards the pending count with no write.
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Cancel (Esc) — discard count, nothing recorded',
    }),
  );
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe('—');
});

test('confirming Scrap records one auditable SCRAPPED operation', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  fireEvent.click(
    screen.getByRole('button', { name: /Scrap damaged quantity/ }),
  );
  const scrapInput = screen.getByLabelText('Scrap barcode input');
  fireEvent.change(scrapInput, { target: { value: 'PF:SCRAP' } });
  fireEvent.keyDown(scrapInput, { key: 'Enter' });
  fireEvent.change(screen.getByLabelText('Common scrap reason (required)'), {
    target: { value: 'gouged face' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm scrap' }));

  expect(
    screen.getByText('SCRAPPED — 2027-60-8114-00 × 1 at Lathe'),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-feedback .t2')?.textContent).toContain(
    'introduced = active + stocked + scrapped',
  );
});

test('PF:SCRAP in the main scan input is rejected outside the workflow', async () => {
  await renderStation();

  scan('PF:SCRAP');
  expect(
    screen.getByText(/accepted only inside the Scrap workflow/),
  ).toBeInTheDocument();
});

/* ============ Worker scans and Last Scanned PN ============ */

test('a Worker scan switches the session and never replaces the Last Scanned PN', async () => {
  await renderStation();

  // Complete one PN operation first.
  scan('PF:PN:118-052');
  fireEvent.keyDown(activeDialog(), { key: 'Enter' });
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe('118-052');

  scan('PF:WORKER:88');
  expect(screen.getByText('Worker session: V. Tran')).toBeInTheDocument();
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe('118-052');
});

/* ============ Undo ============ */

test('Undo shows a summary confirmation, reverses, then advances to the previous operation', async () => {
  await renderStation();

  const undoButton = () => screen.getByRole('button', { name: '⟲ UNDO' });
  expect(undoButton()).toBeDisabled(); // nothing eligible yet

  // Two completed PN operations.
  scan('PF:PN:118-052');
  fireEvent.keyDown(activeDialog(), { key: 'Enter' });
  scan('PF:PN:78-04-0031');
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  fireEvent.keyDown(activeDialog(), { key: 'Enter' });
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe(
    '78-04-0031',
  );

  // Cancel performs no write.
  fireEvent.click(undoButton());
  const dialog = await screen.findByRole('dialog', {
    name: 'Undo last PN operation?',
  });
  expect(dialog).toHaveTextContent('78-04-0031');
  expect(dialog).toHaveTextContent('TRANSFERRED');
  expect(dialog).toHaveTextContent('Mill → Lathe');
  expect(dialog).toHaveTextContent('Effect of the reversal');
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep the operation' }),
  );
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe(
    '78-04-0031',
  );

  // Confirmed Undo reverses and advances to the next eligible action.
  fireEvent.click(undoButton());
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Undo' }));
  expect(
    screen.getByText('Undo recorded as REVERSED — 78-04-0031'),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe('118-052');

  // Undo the remaining operation; afterwards nothing is eligible.
  fireEvent.click(undoButton());
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Undo' }));
  expect(document.querySelector('.ss-lastpn .p')?.textContent).toBe('—');
  expect(undoButton()).toBeDisabled();
});

/* ============ Quantity keypad keyboard behavior ============ */

test('the quantity dialog opens focused and maps physical keys centrally', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', { name: 'Enter quantity' });

  // The numeric entry is a real focusable input, focused on open.
  const qty = screen.getByLabelText('Quantity: 4');
  expect(qty.tagName).toBe('INPUT');
  expect(qty).toHaveAttribute('inputmode', 'numeric');
  expect(qty).toHaveFocus();

  // Delete clears; digits append; Backspace removes; Space is ignored.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  expect(screen.getByLabelText('Quantity: none')).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: '3' });
  fireEvent.keyDown(dialog, { key: ' ' });
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Backspace' });
  expect(screen.getByLabelText('Quantity: none')).toBeInTheDocument();

  // Enter with an invalid value confirms nothing.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.getByRole('dialog')).toBeInTheDocument();

  // A quantity above the available source is rejected by validation:
  // Confirm stays disabled (server-side validation stays documented).
  fireEvent.keyDown(dialog, { key: '9' });
  fireEvent.keyDown(dialog, { key: '9' });
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/118-052 × 2 → Lathe queue/)).toBeInTheDocument();
});

test('virtual keypad buttons cannot capture Space or Enter afterwards', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', { name: 'Enter quantity' });

  // Keypad buttons are type="button" and non-focusable.
  const keypadButtons = Array.from(
    dialog.querySelectorAll<HTMLButtonElement>('.keypad button'),
  );
  expect(keypadButtons.length).toBeGreaterThan(10);
  for (const button of keypadButtons) {
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveAttribute('tabindex', '-1');
  }

  // Clicking a keypad digit then pressing Enter confirms the dialog —
  // the click never leaves the button armed for Enter or Space.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  const three = keypadButtons.find((b) => b.textContent === '3')!;
  fireEvent.mouseDown(three);
  fireEvent.click(three);
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/118-052 × 3 → Lathe queue/)).toBeInTheDocument();
});

test('MAX exists for transfers; Escape always cancels with no write', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', { name: 'Enter quantity' });
  // MAX shortcut restores the maximum after edits.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.click(screen.getByRole('button', { name: 'MAX 4' }));
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();

  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
});

/* ============ Monitoring row actions ============ */

test('queued rows offer ASSIGN and Machine rows offer QUEUE (one-shot)', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary')!;
  const assignButtons = within(summary as HTMLElement).getAllByRole('button', {
    name: 'ASSIGN',
  });
  expect(assignButtons.length).toBeGreaterThan(0);
  const queueButtons = within(summary as HTMLElement).getAllByRole('button', {
    name: 'QUEUE',
  });
  expect(queueButtons.length).toBeGreaterThan(0);

  fireEvent.click(queueButtons[0]);
  const dialog = await screen.findByRole('dialog', {
    name: 'Return quantity to the Area queue',
  });
  expect(dialog).toHaveTextContent('RELEASED_FROM_MACHINE');
  fireEvent.keyDown(dialog, { key: 'Escape' });

  fireEvent.click(assignButtons[0]);
  expect(
    await screen.findByRole('dialog', { name: 'One-shot Machine assignment' }),
  ).toBeInTheDocument();
});

/* ============ Connectivity ============ */

test('recovery re-enables and refocuses the scan input', async () => {
  failing = true;
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01');
  render(<App />);
  await screen.findByText('OFFLINE');
  const input = await screen.findByLabelText('Scan barcode');
  expect(input).toBeDisabled();

  failing = false;
  fireEvent(window, new Event('online'));

  await waitFor(() => expect(input).toBeEnabled());
  await waitFor(() => expect(input).toHaveFocus());
});
