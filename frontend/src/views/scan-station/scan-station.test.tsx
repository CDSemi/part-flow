import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Scan Station regressions for the PN-centric one-shot redesign and
// the multi-step confirmation wizards: station selection routing, no
// Machine Session, Machine-first and PN-first assignment wizards with
// barcode selection, explicit quantity-source selection, dedicated
// confirmation views for every production action (no write before the
// final confirmation), the ENTER-free Scan Barcode card, floating
// notifications, PN row layout/action visibility, Undo confirmation,
// and quantity-keypad keyboard behavior.

let failing = false;

beforeEach(() => {
  failing = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      failing
        ? Promise.reject(new TypeError('Failed to fetch'))
        : Promise.resolve(
            new Response(JSON.stringify({ status: 'ok' }), {
              status: 200,
            }),
          ),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId = 'LATHE-ST-01', query = '') {
  window.history.replaceState({}, '', `/scan-station/${stationId}${query}`);
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

/** Enter on the quantity step advances; Enter on confirmation records. */
function enterThroughConfirmation() {
  fireEvent.keyDown(activeDialog(), { key: 'Enter' });
  fireEvent.keyDown(activeDialog(), { key: 'Enter' });
}

function lastPnText() {
  return document.querySelector('.ss-lastpn .p')?.textContent;
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

/* ============ Scan Barcode card ============ */

test('the Scan Barcode card has no ENTER button; manual entry sits in the scan row', async () => {
  const input = await renderStation();

  // No dedicated ENTER button anywhere — the scanner submits with
  // Enter, and the placeholder says so.
  expect(screen.queryByRole('button', { name: 'ENTER' })).toBeNull();
  expect(input).toHaveAttribute(
    'placeholder',
    'Scan PN / Worker / Machine barcode… (ENTER)',
  );

  // Manual PN entry occupies the scan-row secondary position; the old
  // separate manual-entry row is gone (the hint remains as text only).
  const manual = screen.getByRole('button', { name: '⌨ Enter PN manually' });
  expect(manual.closest('.ss-scanrow')).not.toBeNull();
  expect(document.querySelector('.ss-manual')).toBeNull();
  expect(document.querySelector('.ss-manualcap')).not.toBeNull();

  // No permanently reserved feedback block below Last scanned PN.
  expect(document.querySelector('.ss .ss-feedback')).toBeNull();
  expect(document.querySelector('.ss-lastpn')).not.toBeNull();
});

test('scan feedback floats as a single closable notification', async () => {
  await renderStation();

  scan('NOT-A-PARTFLOW-BARCODE');
  const toast = document.querySelector('.ss-toast.err');
  expect(toast).not.toBeNull();
  expect(toast).toHaveAttribute('role', 'alert');
  expect(toast?.textContent).toContain('Unrecognized barcode');

  // Only the most recent notification shows.
  scan('PF:WORKER:88');
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
  expect(document.querySelector('.ss-toast.ok')).not.toBeNull();
  expect(document.querySelector('.ss-toast.ok')).toHaveAttribute(
    'role',
    'status',
  );

  // The explicit close button dismisses it.
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
  expect(document.querySelector('.ss-toast')).toBeNull();
});

test('notifications auto-dismiss (~4 s success, ~8 s error) and reset on replacement', async () => {
  await renderStation();
  vi.useFakeTimers();

  // Success: gone after ~4 s.
  scan('PF:WORKER:88');
  expect(document.querySelector('.ss-toast.ok')).not.toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3900);
  });
  expect(document.querySelector('.ss-toast.ok')).not.toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(document.querySelector('.ss-toast')).toBeNull();

  // Error: still visible after 4 s, gone after ~8 s.
  scan('NOT-A-PARTFLOW-BARCODE');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4100);
  });
  expect(document.querySelector('.ss-toast.err')).not.toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(document.querySelector('.ss-toast')).toBeNull();

  // A replacing notification restarts its own timer.
  scan('PF:WORKER:88');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  scan('PF:WORKER:12');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(document.querySelector('.ss-toast.ok')).not.toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1100);
  });
  expect(document.querySelector('.ss-toast')).toBeNull();
});

test('the OFFLINE banner is persistent and separate from the notifications', async () => {
  failing = true;
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01');
  render(<App />);

  const offline = await screen.findByText('OFFLINE');
  // The banner is not a floating notification and never auto-dismisses.
  expect(offline.closest('.ss-toast')).toBeNull();
  expect(offline).toBeInTheDocument();
});

/* ============ PN rows: layout, scrap text, action visibility ============ */

test('PN rows show scrapped quantity only as readable text — no ⊘ indicator', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary')!;
  expect(summary.textContent).toContain('1 scrapped');
  // The compact ⊘ indicator is gone — scrap is never displayed twice.
  expect(document.body.textContent).not.toContain('⊘1');
  expect(document.querySelector('.mc-list .scrap')).toBeNull();
  const scrapTexts = summary.querySelectorAll('.r4 .scraptxt');
  expect(scrapTexts.length).toBeGreaterThan(0);
});

test('PN rows use the grid layout: context+quantity right, status line, tooltip WO', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary')!;
  const rows = Array.from(summary.querySelectorAll('.mc-list li'));
  expect(rows.length).toBeGreaterThan(0);
  for (const row of rows) {
    // Quantity is formatted with pcs and anchored in the line-1 right cell.
    expect(row.querySelector('.r1 .r1r .qtyline')?.textContent).toMatch(
      /\d+ pcs/,
    );
    // WO/Job may truncate but stays fully available via the tooltip.
    const wo = row.querySelector<HTMLElement>('.r2 .wo');
    expect(wo?.getAttribute('title')).toBe(wo?.textContent);
  }
  // In-Area status renders as its own line.
  expect(summary.textContent).toContain('Awaiting Machine');
  const machineCard = document.querySelector('.am-machines .abd-machine')!;
  expect(machineCard.textContent).not.toContain('Awaiting Machine');
});

test('In this Area now has no row actions; Machine cards offer only QUEUE', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary')! as HTMLElement;
  expect(within(summary).queryByRole('button', { name: 'ASSIGN' })).toBeNull();
  expect(within(summary).queryByRole('button', { name: 'QUEUE' })).toBeNull();

  const machineRegion = document.querySelector('.am-machines')! as HTMLElement;
  expect(
    within(machineRegion).queryByRole('button', { name: 'ASSIGN' }),
  ).toBeNull();
  const queueButtons = within(machineRegion).getAllByRole('button', {
    name: 'QUEUE',
  });
  expect(queueButtons.length).toBeGreaterThan(0);
  // The action lives in its own separated action cell.
  expect(queueButtons[0].closest('.actcell')).not.toBeNull();
  expect(
    queueButtons[0].closest('li')?.querySelector('.rowmain'),
  ).not.toBeNull();
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

test('the Machine-first wizard: PN pick → MAX quantity → confirmation → record once', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  // Step 1 — Machine preselected from the scan; Next needs the PN too.
  expect(
    within(dialog as HTMLElement).getByRole('button', { name: /Lathe 1/ }),
  ).toHaveClass('sel');
  const next = screen.getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled();

  fireEvent.click(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  fireEvent.click(next);

  // Step 2 — quantity defaults to MAX (queued 2), context recap shown.
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();
  expect(dialog.textContent).toContain('Assigning to');
  expect(dialog.textContent).toContain('MAX defaults to the queued quantity');
  expect(lastPnText()).toBe('—'); // nothing recorded yet

  // Step 3 — dedicated confirmation summary; only Confirm records.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('ASSIGNED_TO_MACHINE');
  expect(dialog.textContent).toContain('Remaining queued after assignment');
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(lastPnText()).toBe('—'); // still nothing recorded

  fireEvent.click(screen.getByRole('button', { name: 'Confirm assignment' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/2027-60-8114-00 × 2 → Lathe 1/)).toBeInTheDocument();
  // The completed action becomes the Last Scanned PN.
  expect(lastPnText()).toBe('2027-60-8114-00');
});

test('the PN-first assignment wizard preselects the PN and can go Back', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  fireEvent.click(
    screen.getByRole('button', { name: /Assign queued quantity/ }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'One-shot Machine assignment',
  });
  // PN preselected, Machine still open — Next stays disabled.
  expect(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  ).toHaveClass('sel');
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

  // Back returns to the PN action dialog (a meaningful previous view).
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(
    await screen.findByRole('dialog', {
      name: 'Choose the action for this PN',
    }),
  ).toBeInTheDocument();
});

test('Step 1 barcode selection: Machine and queued PN scans select; invalid scans error inline', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  const scanField = screen.getByLabelText('Scan Machine or queued PN barcode');
  expect(scanField).toHaveAttribute(
    'placeholder',
    'Scan Machine or queued PN barcode… (ENTER)',
  );

  function scanInside(value: string) {
    fireEvent.change(scanField, { target: { value } });
    fireEvent.keyDown(scanField, { key: 'Enter' });
  }

  // A queued PN barcode completes the pair but does not advance.
  scanInside('PF:PN:2027-60-8114-00');
  expect(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  ).toHaveClass('sel');
  expect(dialog.textContent).toContain('One-shot Machine assignment');
  expect(lastPnText()).toBe('—');

  // Invalid scans: inline error, no selection change, nothing recorded.
  scanInside('PF:MACHINE:ZZ');
  expect(dialog.textContent).toContain('Unknown Machine barcode');
  scanInside('PF:MACHINE:S1');
  expect(dialog.textContent).toContain('belongs to another Area');
  scanInside('PF:MACHINE:L4');
  expect(dialog.textContent).toContain('inactive (maintenance)');
  scanInside('PF:PN:118-052');
  expect(dialog.textContent).toContain('no queued quantity in this Area');
  scanInside('PF:WORKER:88');
  expect(dialog.textContent).toContain('selection unchanged');
  expect(
    within(dialog as HTMLElement).getByRole('button', { name: /Lathe 1/ }),
  ).toHaveClass('sel');

  // A Machine barcode re-selects the Machine.
  scanInside('PF:MACHINE:L2');
  expect(
    within(dialog as HTMLElement).getByRole('button', { name: /Lathe 2/ }),
  ).toHaveClass('sel');

  // Complete: Next → quantity (MAX) → Enter → confirmation → confirm.
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm assignment' }));
  expect(screen.getByText(/2027-60-8114-00 × 2 → Lathe 2/)).toBeInTheDocument();
});

test('more than 6 queued PNs use a compact dropdown instead of buttons', async () => {
  await renderStation('LATHE-ST-01', '?state=long');

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  // Machines (4) keep explicit buttons; queued PNs (>6) use a select.
  expect(
    within(dialog as HTMLElement).getByRole('button', { name: /Lathe 1/ }),
  ).toBeInTheDocument();
  const pnSelect = screen.getByLabelText('PN (queued)');
  expect(pnSelect.tagName).toBe('SELECT');
  const options = Array.from(
    (pnSelect as HTMLSelectElement).options,
    (o) => o.text,
  );
  expect(options.length).toBeGreaterThan(7);
  // Options display PN and queued quantity.
  expect(options.some((o) => /queued \d+/.test(o))).toBe(true);

  fireEvent.change(pnSelect, { target: { value: '2027-60-8114-00' } });
  expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
});

test('Back preserves Machine, PN and an edited quantity', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  fireEvent.click(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));

  // Edit the quantity, go to confirmation, then Back twice.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '1' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('ASSIGNED_TO_MACHINE');
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Quantity: 1')).toBeInTheDocument(); // preserved
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  // Step 1 still carries both selections.
  expect(
    within(dialog as HTMLElement).getByRole('button', { name: /Lathe 1/ }),
  ).toHaveClass('sel');
  expect(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  ).toHaveClass('sel');
  // Forward again: the edited quantity survived Back.
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByLabelText('Quantity: 1')).toBeInTheDocument();

  // Escape cancels the whole wizard — nothing recorded, nothing armed.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(lastPnText()).toBe('—');
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
  expect(lastPnText()).toBe('—');
});

test('a PN elsewhere with one source goes to quantity, then a confirmation view', async () => {
  await renderStation();

  scan('PF:PN:118-052'); // only at Manual (4 pcs)
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });
  expect(dialog).toHaveTextContent('Transfer Manual → Lathe');
  // Guidance sits with the input; MAX defaults to the source quantity.
  expect(dialog).toHaveTextContent('Available at Manual: 4 pcs');
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();

  // Enter advances to the dedicated confirmation view — no write yet.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('TRANSFERRED');
  expect(dialog.textContent).toContain('Remaining at source');
  expect(lastPnText()).toBe('—');

  // Enter on the confirmation view performs the final confirm.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/118-052 × 4 → Lathe queue/)).toBeInTheDocument();
});

test('Back on the transfer confirmation preserves the entered quantity', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = activeDialog();
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('Confirm transfer');

  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();

  // Cancel still abandons everything with no write.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
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
    name: 'Transfer into this Area',
  });
  expect(qtyDialog).toHaveTextContent('Transfer Deburr → Lathe');
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
});

/* ============ Intake wizard (no active WO Demand) ============ */

test('an unknown PN opens the three-step intake wizard with editable defaults', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive quantity — intake',
  });
  expect(dialog).toHaveTextContent('created on first valid use');
  // Step 1 — settings only, no quantity input yet.
  expect(dialog.querySelector('.qtydisplay')).toBeNull();
  expect(screen.getByLabelText('Request Type')).toHaveValue('MODIFY');
  expect(screen.getByLabelText('Route Mode')).toHaveValue('FLOATING');
  fireEvent.change(screen.getByLabelText('Request Type'), {
    target: { value: 'NEW' },
  });
  expect(screen.getByLabelText('Request Type')).toHaveValue('NEW');
  fireEvent.change(screen.getByLabelText('Request Type'), {
    target: { value: 'MODIFY' },
  });

  // Step 2 — recap + guidance directly above the quantity input.
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(dialog.textContent).toContain('MODIFY · FLOATING');
  expect(dialog.textContent).toContain(
    'Enter the physical quantity received. No default quantity is assumed.',
  );
  fireEvent.keyDown(dialog, { key: '6' });

  // Step 3 — structured confirmation; only Confirm intake records.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('Receive quantity — intake (RECEIVED)');
  expect(dialog.textContent).toContain(
    'Creates an internal Work Order without an external number',
  );
  expect(lastPnText()).toBe('—');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm intake' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByText(/NEW-PART-01 × 6 received into Lathe queue/),
  ).toBeInTheDocument();
  // Internal Work Order without an external number — no TMP number.
  const detail = document.querySelector('.ss-toast .t2')?.textContent ?? '';
  expect(detail).toContain('without an external number (displays —)');
  expect(detail).not.toMatch(/TMP-/);
});

test('intake Back preserves settings and quantity; Cancel records nothing', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const dialog = activeDialog();
  fireEvent.change(screen.getByLabelText('Request Type'), {
    target: { value: 'NEW' },
  });
  fireEvent.change(screen.getByLabelText('Reason / notes'), {
    target: { value: 'first article' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(dialog.textContent).toContain('NEW · FLOATING');
  fireEvent.keyDown(dialog, { key: '3' });

  // Back to settings — everything entered is preserved.
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Request Type')).toHaveValue('NEW');
  expect(screen.getByLabelText('Reason / notes')).toHaveValue('first article');

  // Forward again: the quantity survived too.
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();

  // Confirmation shows the structured values.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('NEW');
  expect(dialog.textContent).toContain('first article');

  // Cancel on the confirmation step records nothing.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
});

test('PN identity is case-insensitive and keeps the first-entered casing', async () => {
  await renderStation();

  scan('PF:PN:abc-part');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive quantity — intake',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.keyDown(dialog, { key: '1' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
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

test('Add more quantity requires a reason, has no MAX/default, and confirms separately', async () => {
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
  const next = screen.getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled(); // reason still missing

  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: 'found 2 extra blanks' },
  });
  expect(next).toBeEnabled();
  fireEvent.click(next);

  // The dedicated confirmation clearly identifies the adjustment.
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('QUANTITY_ADJUSTED · INCREASE');
  expect(dialog.textContent).toContain('found 2 extra blanks');
  expect(lastPnText()).toBe('—');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm addition' }));

  expect(
    screen.getByText(/0455-20-0118-03 \+2 pcs at Lathe/),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-toast .t2')?.textContent).toContain(
    'never changes the WO Demand requested quantity',
  );
});

/* ============ Repair ============ */

test('the Repair intent is explicit: source, quantity, reason, then a Repair confirmation', async () => {
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
  const next = screen.getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled(); // reason is required

  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: 'shoulder cut short' },
  });
  expect(next).toBeEnabled();
  fireEvent.click(next);

  // The confirmation explicitly identifies the Repair movement.
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('TRANSFERRED · movement_reason REPAIR');
  expect(lastPnText()).toBe('—');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm repair' }));

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

  // Available / pending / remaining stay visible while counting.
  expect(dialog.textContent).toContain('Available at Lathe');

  // Next stays blocked until the common reason exists.
  const next = screen.getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Common scrap reason (required)'), {
    target: { value: 'tool crash' },
  });
  expect(next).toBeEnabled();
  fireEvent.click(next);

  // The dedicated confirmation shows available, scrap and remaining.
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('SCRAPPED');
  expect(dialog.textContent).toContain('6 pcs'); // available
  expect(dialog.textContent).toContain('2 pcs'); // scrap quantity
  expect(dialog.textContent).toContain('4 pcs'); // remaining
  expect(lastPnText()).toBe('—');

  // Cancel discards the pending count with no write.
  fireEvent.click(
    screen.getAllByRole('button', {
      name: 'Cancel (Esc) — discard count, nothing recorded',
    })[0],
  );
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
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
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm scrap' }));

  expect(
    screen.getByText('SCRAPPED — 2027-60-8114-00 × 1 at Lathe'),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-toast .t2')?.textContent).toContain(
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

/* ============ Queue return ============ */

test('QUEUE return uses quantity then a dedicated confirmation view', async () => {
  await renderStation();

  const machineRegion = document.querySelector('.am-machines')! as HTMLElement;
  fireEvent.click(
    within(machineRegion).getAllByRole('button', { name: 'QUEUE' })[0],
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Return quantity to the Area queue',
  });
  expect(dialog).toHaveTextContent('MAX is selected by default');

  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('RELEASED_FROM_MACHINE');
  expect(dialog.textContent).toContain('Remaining on Machine');
  expect(lastPnText()).toBe('—');

  fireEvent.click(screen.getByRole('button', { name: 'Confirm queue return' }));
  expect(screen.getByText(/returned to the Lathe queue/)).toBeInTheDocument();
});

/* ============ Worker scans and Last Scanned PN ============ */

test('a Worker scan switches the session and never replaces the Last Scanned PN', async () => {
  await renderStation();

  // Complete one PN operation first (quantity → confirmation → confirm).
  scan('PF:PN:118-052');
  enterThroughConfirmation();
  expect(lastPnText()).toBe('118-052');

  scan('PF:WORKER:88');
  expect(screen.getByText('Worker session: V. Tran')).toBeInTheDocument();
  expect(lastPnText()).toBe('118-052');
});

/* ============ Undo ============ */

test('Undo shows a summary confirmation, reverses, then advances to the previous operation', async () => {
  await renderStation();

  const undoButton = () => screen.getByRole('button', { name: '⟲ UNDO' });
  expect(undoButton()).toBeDisabled(); // nothing eligible yet

  // Two completed PN operations.
  scan('PF:PN:118-052');
  enterThroughConfirmation();
  scan('PF:PN:78-04-0031');
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  enterThroughConfirmation();
  expect(lastPnText()).toBe('78-04-0031');

  // Cancel performs no write.
  fireEvent.click(undoButton());
  const dialog = await screen.findByRole('dialog', {
    name: 'Undo last PN operation?',
  });
  expect(dialog).toHaveTextContent('78-04-0031');
  expect(dialog).toHaveTextContent('TRANSFERRED');
  expect(dialog).toHaveTextContent('Mill → Lathe');
  expect(dialog).toHaveTextContent('Effect of the reversal');
  // Undo shares the structured confirmation presentation.
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel — keep the operation' }),
  );
  expect(lastPnText()).toBe('78-04-0031');

  // Confirmed Undo reverses and advances to the next eligible action.
  fireEvent.click(undoButton());
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Undo' }));
  expect(
    screen.getByText('Undo recorded as REVERSED — 78-04-0031'),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('118-052');

  // Undo the remaining operation; afterwards nothing is eligible.
  fireEvent.click(undoButton());
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Undo' }));
  expect(lastPnText()).toBe('—');
  expect(undoButton()).toBeDisabled();
});

/* ============ Quantity keypad keyboard behavior ============ */

test('the quantity step opens focused and maps physical keys centrally', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });

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

  // Enter with an invalid value advances nowhere.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).toBeNull();
  expect(screen.getByRole('dialog')).toBeInTheDocument();

  // A quantity above the available source is rejected by validation:
  // Next stays disabled and the message explains the limit.
  fireEvent.keyDown(dialog, { key: '9' });
  fireEvent.keyDown(dialog, { key: '9' });
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  expect(dialog.textContent).toContain(
    'Quantity cannot exceed the 4 pcs currently available at the source.',
  );
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/118-052 × 2 → Lathe queue/)).toBeInTheDocument();
});

test('virtual keypad buttons cannot capture Space or Enter afterwards', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });

  // Keypad buttons are type="button" and non-focusable.
  const keypadButtons = Array.from(
    dialog.querySelectorAll<HTMLButtonElement>('.keypad button'),
  );
  expect(keypadButtons.length).toBeGreaterThan(10);
  for (const button of keypadButtons) {
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveAttribute('tabindex', '-1');
  }

  // Clicking a keypad digit then pressing Enter advances the wizard —
  // the click never leaves the button armed for Enter or Space.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  const three = keypadButtons.find((b) => b.textContent === '3')!;
  fireEvent.mouseDown(three);
  fireEvent.click(three);
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('Confirm transfer');
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/118-052 × 3 → Lathe queue/)).toBeInTheDocument();
});

test('MAX exists for transfers; Escape always cancels with no write', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });
  // MAX shortcut restores the maximum after edits.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.click(screen.getByRole('button', { name: 'MAX 4' }));
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();

  // Escape cancels even from the confirmation view — nothing recorded.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('Confirm transfer');
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
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
