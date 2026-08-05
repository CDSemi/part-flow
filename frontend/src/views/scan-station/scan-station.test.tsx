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
  const lathe = screen
    .getByRole('button', { name: 'Open LATHE-ST-01' })
    .closest('.ss-stationcard') as HTMLElement;
  expect(lathe).toHaveTextContent('Machine Shop');
  expect(lathe).toHaveTextContent('4 Machines — queue & assign');
  const deburr = screen
    .getByRole('button', { name: 'Open DEBURR-ST-01' })
    .closest('.ss-stationcard') as HTMLElement;
  expect(deburr).toHaveTextContent('No Machines — direct Area processing');
});

test('the Station Selector renders Operations as individual chips', async () => {
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);
  await screen.findByRole('heading', { name: 'Select a Scan Station' });

  // Single-Operation station: one chip.
  const lathe = screen
    .getByRole('button', { name: 'Open LATHE-ST-01' })
    .closest('.ss-stationcard') as HTMLElement;
  expect(
    Array.from(lathe.querySelectorAll('.opchip'), (el) => el.textContent),
  ).toEqual(['Turning']);

  // Multi-Operation station: one chip per Operation, never one
  // comma-joined text run.
  const external = screen
    .getByRole('button', { name: 'Open EXT-ST-01' })
    .closest('.ss-stationcard') as HTMLElement;
  expect(
    Array.from(external.querySelectorAll('.opchip'), (el) => el.textContent),
  ).toEqual(['Plating', 'Painting', 'Testing']);
  expect(external.textContent).not.toContain('Plating, Painting');
});

test('the station card main surface opens standard mode; Production mode is a sibling action', async () => {
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);
  await screen.findByRole('heading', { name: 'Select a Scan Station' });

  // The card's main selection surface is ONE full-card button (no
  // nested interactive controls inside it) and opens standard mode.
  const main = screen.getByRole('button', { name: 'Open LATHE-ST-01' });
  expect(main.classList.contains('ss-stationmain')).toBe(true);
  expect(main.querySelector('button, a, input, select')).toBeNull();
  // There is no separate `Open` button anymore.
  expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
  fireEvent.click(main);
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');
  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
});

test('the Production mode card action opens the production route', async () => {
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);
  await screen.findByRole('heading', { name: 'Select a Scan Station' });

  // Accessible name carries the Station ID; the visible label is
  // exactly `Production mode`, a sibling of the full-card button.
  const prod = screen.getByRole('button', {
    name: 'Open LATHE-ST-01 in production mode',
  });
  expect(prod.textContent).toBe('Production mode');
  expect(prod.closest('.ss-stationmain')).toBeNull();
  fireEvent.click(prod);
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01/production');
  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
});

test('an unknown Station ID is an explicit error, never a fallback', async () => {
  window.history.replaceState({}, '', '/scan-station/GHOST-ST-99');
  render(<App />);

  expect(
    await screen.findByText(/Unknown or inactive Scan Station “GHOST-ST-99”/),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Scan barcode')).not.toBeInTheDocument();
});

test('one coherent non-interactive footer in standard mode', async () => {
  await renderStation();

  // Faint Station ID, subtle mode label, and the shortcut hint — no
  // clickable Station ID (the top navigation's Scan Station entry
  // already returns to the Station Selector; the footer never
  // duplicates it).
  const foot = document.querySelector('.ss-stationfoot') as HTMLElement;
  expect(foot).toHaveTextContent('LATHE-ST-01');
  expect(foot).toHaveTextContent('Standard mode');
  expect(foot).toHaveTextContent('Ctrl+Shift+K: switch mode');
  expect(foot.querySelector('button')).toBeNull();
  expect(screen.queryByRole('button', { name: 'LATHE-ST-01' })).toBeNull();
});

/* ============ Production mode ============ */

test('the standard station keeps the top navigation', async () => {
  await renderStation();

  expect(
    screen.getByRole('navigation', { name: 'Primary' }),
  ).toBeInTheDocument();
  // Both modes document the mode-switch shortcut in the footer.
  expect(document.querySelector('.ss-stationfoot')?.textContent).toContain(
    'Ctrl+Shift+K',
  );
  expect(document.querySelector('.ss')?.className).not.toContain('production');
});

test('production mode hides the top navigation and keeps the station working', async () => {
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01/production');
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');

  // No top application navigation, no Production Board / Management /
  // Administration links.
  expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Production Board' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Management' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Administration' })).toBeNull();

  // The station root carries the production class → full viewport
  // height (no leftover 53px top-nav offset, scan-station.css).
  expect(document.querySelector('.ss')?.className).toContain('production');

  // A small non-interactive Production mode label; the Station ID is
  // visible but NOT a button — no casual station switching.
  expect(screen.getByText('Production mode')).toBeInTheDocument();
  expect(screen.getByText('Production mode').tagName).not.toBe('BUTTON');
  const foot = document.querySelector('.ss-stationfoot') as HTMLElement;
  expect(foot).toHaveTextContent('LATHE-ST-01');
  expect(foot.querySelector('button')).toBeNull();

  // Connectivity status stays visible inside the Scan Station header.
  expect(
    screen.getByRole('status', { name: /Backend connection/ }),
  ).toBeInTheDocument();

  // Scanning still works: a PN scan opens its one-shot dialog.
  fireEvent.change(input, {
    target: { value: 'PF:PN:2027-60-8114-00' },
  });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});

test('the OFFLINE banner stays visible in production mode', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('offline'))),
  );
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01/production');
  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent(/OFFLINE/);
  expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
});

test('production mode offers the global theme control in the header actions group', async () => {
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01/production');
  render(<App />);
  await screen.findByLabelText('Scan barcode');

  // Connectivity chip and Dark/Light control form one header group.
  const actions = document.querySelector(
    '.ss-head .ss-headactions',
  ) as HTMLElement;
  expect(actions).not.toBeNull();
  expect(actions.querySelector('.connchip')).not.toBeNull();
  // Vertical, right-aligned stack: connectivity status first, the
  // theme control directly beneath it (layout in scan-station.css).
  const children = Array.from(actions.children, (el) => el.className);
  expect(children[0]).toContain('connchip');
  expect(children[1]).toContain('themetoggle');
  const toggle = within(actions).getByRole('button', { name: '🌙 Dark' });
  expect(toggle).toHaveAttribute('aria-pressed', 'false');

  // Same global ThemeProvider state: toggling updates the whole
  // application instantly (the theme class lives on <body>).
  fireEvent.click(toggle);
  expect(document.body.classList.contains('light')).toBe(true);
  expect(
    within(actions).getByRole('button', { name: '☀️ Light' }),
  ).toHaveAttribute('aria-pressed', 'true');

  // The choice survives switching to the standard route, where the
  // header control disappears and the top-navigation control returns.
  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');
  expect(document.body.classList.contains('light')).toBe(true);
  expect(document.querySelector('.ss-headactions')).toBeNull();
  const nav = screen.getByRole('navigation', { name: 'Primary' });
  expect(
    within(nav).getByRole('button', { name: '☀️ Light' }),
  ).toBeInTheDocument();
});

test('the production header wraps the Worker pill and header actions in one group', async () => {
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01/production');
  render(<App />);
  await screen.findByLabelText('Scan barcode');
  await screen.findByText('ONLINE');

  // v15: one shared `.ss-headgroup` cell holds the Worker pill and the
  // header actions — the production header keeps three cells like
  // standard mode, with no separate bordered actions column.
  const head = document.querySelector('.ss-head')!;
  expect(Array.from(head.children, (el) => el.className)).toEqual([
    'ss-id',
    'ss-stats',
    'ss-headgroup',
  ]);
  const group = head.querySelector('.ss-headgroup')!;
  expect(Array.from(group.children, (el) => el.className)).toEqual([
    'ss-pill',
    'ss-headactions',
  ]);
  // The actions hold the connectivity chip (its own chip frame) and
  // the compact theme control.
  const actions = group.querySelector('.ss-headactions') as HTMLElement;
  expect(actions.querySelector('.connchip')?.textContent).toContain('ONLINE');
  expect(
    within(actions).getByRole('button', { name: '🌙 Dark' }),
  ).toBeInTheDocument();
});

test('Ctrl+Shift+K toggles between standard and production routes only', async () => {
  await renderStation();

  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01/production');
  await screen.findByText('Production mode');

  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');

  // Without the full chord nothing toggles (K alone is wedge input).
  fireEvent.keyDown(window, { key: 'K', ctrlKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');
});

test('the shortcut works from the barcode input but never from dialogs or other fields', async () => {
  const input = await renderStation();

  // Focus discipline keeps the main barcode input focused almost
  // always — the chord stays usable there (it is a scanner target,
  // not text entry, and the wedge capture ignores modifier chords).
  fireEvent.keyDown(input, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01/production');
  fireEvent.keyDown(input, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');

  // Inside an active dialog workflow the chord is inert.
  scan('PF:PN:2027-60-8114-00');
  fireEvent.keyDown(activeDialog(), {
    key: 'K',
    ctrlKey: true,
    shiftKey: true,
  });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');

  // In any other text field (manual PN entry input) the chord is
  // inert too.
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: '⌨ Enter PN manually' }));
  const manualInput = screen.getByLabelText('Exact PartNumber');
  fireEvent.keyDown(manualInput, { key: 'K', ctrlKey: true, shiftKey: true });
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');
});

/* ============ Area with / without Machines ============ */

test('an Area with Machines shows queue statistics and the shared layout', async () => {
  await renderStation();

  const stats = document.querySelector('.ss-stats');
  expect(stats?.textContent).toContain('Queued');
  expect(stats?.textContent).toContain('On machines');
  expect(stats?.textContent).toContain('Done');
  // Shared layout: left summary card + right-side Machine cards grid.
  const layout = document.querySelector('.am');
  expect(layout).not.toBeNull();
  expect(layout?.classList.contains('am-single')).toBe(false);
  const machineCards = document.querySelectorAll('.am-machines .abd-machine');
  expect(machineCards.length).toBe(4);
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('On Machines');
  expect(summary?.textContent).toContain('Area queue — awaiting Machine');
  expect(summary?.textContent).toContain('Finished — ready to move');
});

test('an Area without Machines renders a full-width card and no Machine region', async () => {
  await renderStation('DEBURR-ST-01');

  // No meaningless queue/Machine statistics — but Processing and Done
  // (the no-Machine reconciliation pair) are present.
  const stats = document.querySelector('.ss-stats');
  expect(stats?.textContent).toContain('Processing');
  expect(stats?.textContent).toContain('Done');
  expect(stats?.textContent).not.toContain('Queued');
  expect(stats?.textContent).not.toContain('On machines');
  // Full-width single-column layout, no Machine cards at all.
  const layout = document.querySelector('.am');
  expect(layout?.classList.contains('am-single')).toBe(true);
  expect(document.querySelector('.abd-machine')).toBeNull();
  // Deburr's whole mock quantity is finished — READY_TO_TRANSFER.
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('Finished — ready to move');
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

test('In this Area now has no row actions; Machine-card rows offer DONE and QUEUE', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary')! as HTMLElement;
  expect(within(summary).queryByRole('button', { name: 'ASSIGN' })).toBeNull();
  expect(
    within(summary).queryByRole('button', { name: 'Return to Area queue' }),
  ).toBeNull();
  expect(
    within(summary).queryByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeNull();

  const machineRegion = document.querySelector('.am-machines')! as HTMLElement;
  expect(
    within(machineRegion).queryByRole('button', { name: 'ASSIGN' }),
  ).toBeNull();
  // Two DISTINCT actions with clear accessible names: DONE completes
  // Area processing; QUEUE returns unfinished quantity to the queue.
  const doneButtons = within(machineRegion).getAllByRole('button', {
    name: 'Complete Area processing',
  });
  const queueButtons = within(machineRegion).getAllByRole('button', {
    name: 'Return to Area queue',
  });
  expect(doneButtons.length).toBeGreaterThan(0);
  expect(queueButtons.length).toBe(doneButtons.length);
  // Distinct wording and visual treatment; icon above the text label
  // (the icon is never the only source of meaning).
  expect(doneButtons[0]).toHaveTextContent('DONE');
  expect(doneButtons[0].classList.contains('done')).toBe(true);
  expect(queueButtons[0]).toHaveTextContent('QUEUE');
  expect(queueButtons[0].classList.contains('done')).toBe(false);
  expect(queueButtons[0].querySelector('.ric')).not.toBeNull();
  // Both actions live in the row's separated action cell.
  expect(doneButtons[0].closest('.actcell')).not.toBeNull();
  expect(queueButtons[0].closest('.actcell')).toBe(
    doneButtons[0].closest('.actcell'),
  );
  expect(
    queueButtons[0].closest('li')?.querySelector('.rowmain'),
  ).not.toBeNull();
});

/* ============ No Machine Session — one-shot Machine scan ============ */

test('a Machine scan opens the Assign to Machine dialog and leaves nothing armed', async () => {
  await renderStation();

  scan('PF:MACHINE:L2');
  const dialog = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  expect(dialog).toHaveTextContent('The assignment applies once');

  // Cancel: no write, no sticky Machine context anywhere.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(document.querySelector('.ss-head')?.textContent).not.toContain(
    'Lathe 2',
  );
});

test('the assignment dialog renders "Assign to Machine" in operator language only', async () => {
  await renderStation();

  scan('PF:MACHINE:L2');
  const dialog = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  expect(
    within(dialog as HTMLElement).getByRole('heading', {
      name: 'Assign to Machine',
    }),
  ).toBeInTheDocument();
  // The temporary behavior is explained naturally; implementation
  // wording (one-shot, armed context, session mechanics) never renders.
  expect(dialog.textContent).toContain('the next scan starts fresh');
  expect(dialog.textContent).not.toMatch(/one-shot/i);
  expect(dialog.textContent).not.toMatch(/armed/i);
  expect(dialog.textContent).not.toMatch(/Machine Session/i);
  fireEvent.keyDown(dialog, { key: 'Escape' });
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
    name: 'Assign to Machine',
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
  expect(dialog.textContent).toContain('Assign to Machine');
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
  // Operator-facing guidance: what was scanned and what confirmation
  // does — never internal record/persistence wording.
  expect(dialog).toHaveTextContent('New PN — not registered yet');
  expect(dialog).toHaveTextContent(
    'confirming this intake registers the PN exactly as shown',
  );
  expect(dialog.textContent).not.toMatch(/record is created/i);
  expect(dialog.textContent).not.toMatch(/case-insensitive/i);
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

  // Step 2 — compact two-line recap (v15): selection chips on one
  // line, the WO/due context on the other, then the instruction
  // guidance directly above the quantity input.
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  const recap = dialog.querySelector('.ss-recap')!;
  expect(recap.textContent).toContain('MODIFY');
  expect(recap.textContent).toContain('FLOATING');
  expect(recap.textContent).toContain('Internal WO —');
  expect(recap.textContent).toContain('Due: —');
  expect(dialog.textContent).toContain(
    'Enter the physical quantity received. No default quantity is assumed.',
  );
  fireEvent.keyDown(dialog, { key: '6' });

  // Step 3 — structured confirmation; only Confirm intake records.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('Receive quantity — intake');
  expect(dialog.textContent).toContain('RECEIVED');
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

test('the intake settings step separates PN identity, WO recap, and quantity guidance', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive quantity — intake',
  });

  // v15: the description carries PN identity only; the Work Order
  // behavior and date handling form a separate recap block.
  expect(dialog.querySelector('.sub')?.textContent).toContain(
    'New PN — not registered yet',
  );
  const recap = dialog.querySelector('.ss-recap')!;
  expect(recap.textContent).toContain(
    'Work Order: Creates an internal Work Order without an external number (displays —).',
  );
  expect(recap.textContent).toContain(
    'Received date: taken from this scan · Due date: optional.',
  );

  // ℹ info guidance: the quantity comes on the next step and nothing
  // is recorded before the final confirmation.
  const guide = dialog.querySelector('.ss-guide.info')!;
  expect(guide.querySelector('.gmark')?.textContent).toBe('ℹ');
  expect(guide.textContent).toContain(
    'The quantity to receive is entered on the next step — nothing is recorded until the final confirmation.',
  );
  // v15: the marker-less `neutral` guidance kind no longer exists.
  expect(dialog.querySelector('.ss-guide.neutral')).toBeNull();
  fireEvent.keyDown(dialog, { key: 'Escape' });
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
  const recap = dialog.querySelector('.ss-recap')!;
  expect(recap.textContent).toContain('NEW');
  expect(recap.textContent).toContain('FLOATING');
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

test('quantity dialogs keep a dedicated review step separate from quantity entry', async () => {
  await renderStation();

  scan('PF:PN:0455-20-0118-03');
  fireEvent.click(screen.getByRole('button', { name: /Add more quantity/ }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Add more quantity',
  });

  // Step 1 — quantity entry only: no confirmation summary on this step.
  expect(dialog.querySelector('.ss-confirm')).toBeNull();
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: 'found 2 extra blanks' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));

  // Step 2 — the dedicated review step: the quantity input is gone,
  // the structured summary stands alone, and the confirm action holds
  // focus so Enter performs the final confirmation.
  expect(dialog.querySelector('.qtydisplay')).toBeNull();
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain(
    'Review the quantity addition, then confirm.',
  );
  expect(
    screen.getByRole('button', { name: 'Confirm addition' }),
  ).toHaveFocus();
  expect(lastPnText()).toBe('—'); // reviewing is never a write

  // Back returns to entry with everything preserved; Escape cancels
  // the whole workflow from the review step with no write.
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();
  expect(screen.getByLabelText('Reason (required)')).toHaveValue(
    'found 2 extra blanks',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
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
  // Operator wording — the intent is explicit, never inferred.
  expect(dialog).toHaveTextContent('never assumed to be a repair');
  expect(dialog).toHaveTextContent('no new quantity and no new demand');

  // v15 entry recap names the repair destination.
  expect(dialog.querySelector('.ss-recap')?.textContent).toContain(
    'Repair destination: Lathe',
  );

  // The single known source is preselected; quantity defaults to MAX.
  // The MAX/default statement is an ℹ instruction (v15), not a warning.
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();
  const maxGuide = dialog.querySelector('.ss-guide.info')!;
  expect(maxGuide.textContent).toContain('MAX 4 pcs, the default');
  const next = screen.getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled(); // reason is required

  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: 'shoulder cut short' },
  });
  expect(next).toBeEnabled();
  fireEvent.click(next);

  // The confirmation explicitly identifies the Repair movement.
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('TRANSFERRED · REPAIR intent');
  expect(lastPnText()).toBe('—');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm repair' }));

  expect(
    screen.getByText(/Repair — 0455-20-0118-03 × 4 returned to Lathe/),
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

  // Available / pending / remaining stay visible while counting — a
  // plain ℹ status line (v15), not a warning.
  const statusLine = dialog.querySelector('.ss-guide.info')!;
  expect(statusLine.textContent).toContain('Available at Lathe');
  expect(statusLine.querySelector('.gmark')?.textContent).toBe('ℹ');

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
  // The destructive scrap quantity carries the error value tone (v15).
  expect(dialog.querySelector('.ss-confirm dd.tone-err')?.textContent).toBe(
    '2 pcs',
  );
  expect(lastPnText()).toBe('—');

  // Cancel (standard label) discards the pending count with no write.
  fireEvent.click(
    within(dialog as HTMLElement).getByRole('button', {
      name: 'Cancel (Esc)',
    }),
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
    screen.getByText('Scrapped — 2027-60-8114-00 × 1 at Lathe'),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-toast .t2')?.textContent).toContain(
    'never reduces the WO Demand requested quantity',
  );
});

test('scrap instructions describe the workflow without the SCRAPPED event name', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  const actions = await screen.findByRole('dialog', {
    name: 'Choose the action for this PN',
  });
  // The action-choice explanation uses operator wording (v15) — the
  // canonical event name never appears in instructions.
  const choice = within(actions as HTMLElement).getByRole('button', {
    name: /Scrap damaged quantity/,
  });
  expect(choice.textContent).toContain(
    'Scan PF:SCRAP once for each damaged piece, then enter one reason for the entire quantity. Nothing changes until you review and confirm the scrap.',
  );
  expect(choice.textContent).not.toContain('SCRAPPED');

  fireEvent.click(choice);
  const dialog = await screen.findByRole('dialog', {
    name: 'Scrap damaged quantity',
  });
  expect(dialog.querySelector('.sub')?.textContent).toContain(
    'Scan PF:SCRAP once for each damaged piece, then enter one reason for the entire quantity. Nothing changes until you review and confirm the scrap.',
  );
  // SCRAPPED appears only in the confirmation summary's Recorded
  // event row and in history surfaces — never on the counting step.
  expect(dialog.textContent).not.toContain('SCRAPPED');
  fireEvent.keyDown(dialog, { key: 'Escape' });
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
    within(machineRegion).getAllByRole('button', {
      name: 'Return to Area queue',
    })[0],
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
  // The reversal effect carries the warning value tone (v15).
  expect(
    dialog.querySelector('.ss-confirm dd.tone-warn')?.textContent,
  ).toContain('Returns');
  // Undo shares the structured confirmation presentation.
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  // The Undo dialog keeps the standard wizard Cancel label (§3.10).
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(lastPnText()).toBe('78-04-0031');

  // Confirmed Undo reverses and advances to the next eligible action.
  fireEvent.click(undoButton());
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Undo' }));
  expect(screen.getByText('Undo recorded — 78-04-0031')).toBeInTheDocument();
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

test('quantity editing is cursor-aware: selection replace, caret insert, fallback append', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });

  // The MAX default mounts focused with its value selected — a typed
  // digit REPLACES it (2, not 42).
  const qty = screen.getByLabelText('Quantity: 4') as HTMLInputElement;
  expect(qty).toHaveFocus();
  expect(qty.selectionStart).toBe(0);
  expect(qty.selectionEnd).toBe(1);
  fireEvent.keyDown(qty, { key: '2' });
  expect(qty.value).toBe('2');
  expect(qty.selectionStart).toBe(1);

  // A digit inserts at the caret position — 243, not 234 (validation
  // marks it over the limit; editing itself stays cursor-aware).
  fireEvent.keyDown(qty, { key: '3' });
  expect(qty.value).toBe('23');
  qty.setSelectionRange(1, 1);
  fireEvent.keyDown(qty, { key: '4' });
  expect(qty.value).toBe('243');
  expect(qty.selectionStart).toBe(2);
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

  // The on-screen keypad applies the identical transitions: replace a
  // selection at its exact position…
  qty.setSelectionRange(0, 2); // "24" selected
  const one = Array.from(
    dialog.querySelectorAll<HTMLButtonElement>('.keypad button'),
  ).find((b) => b.textContent === '1')!;
  fireEvent.mouseDown(one);
  fireEvent.click(one);
  expect(qty.value).toBe('13');
  expect(qty).toHaveFocus();
  expect(qty.selectionStart).toBe(1);

  // …and the dialog-level fallback (focus outside the input) appends
  // to the end exactly as before.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '3' });
  expect(qty.value).toBe('3');
  fireEvent.keyDown(dialog, { key: 'Escape' });
});

test('guidance kinds are distinct and summaries emphasize operational values', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });

  // Info guidance carries its marker; the required-action prompt and
  // the validation error are separate, progressively stronger kinds.
  const info = dialog.querySelector('.ss-guide.info');
  expect(info?.querySelector('.gmark')?.textContent).toBe('ℹ');
  fireEvent.keyDown(dialog, { key: 'Delete' });
  const action = dialog.querySelector('.ss-guide.action');
  expect(action?.textContent).toContain('Enter a quantity between 1 and 4.');
  expect(action?.querySelector('.gmark')?.textContent).toBe('›');
  fireEvent.keyDown(dialog, { key: '9' });
  fireEvent.keyDown(dialog, { key: '9' });
  const error = dialog.querySelector('.ss-guide.error');
  expect(error?.textContent).toContain('cannot exceed');
  expect(error?.querySelector('.gmark')?.textContent).toBe('✕');
  // Validation preserves the entered value.
  expect((dialog.querySelector('.qtydisplay') as HTMLInputElement).value).toBe(
    '99',
  );

  // Confirmation summary: primary operational values scan first,
  // audit rows stay quiet, and entity chips are labels — never
  // buttons.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  const summary = dialog.querySelector('.ss-confirm')!;
  const primary = Array.from(
    summary.querySelectorAll('dd.primary'),
    (el) => el.textContent ?? '',
  );
  expect(primary.some((t) => t.includes('118-052'))).toBe(true);
  expect(primary.some((t) => t.includes('2 pcs'))).toBe(true);
  const chips = summary.querySelectorAll('.dlgchip');
  expect(chips.length).toBeGreaterThan(0);
  chips.forEach((chip) => expect(chip.tagName).toBe('SPAN'));
  // Area values (Source, Destination) carry the stable Area identity
  // dot inside their chip (v15) — never recolored body text.
  expect(summary.querySelectorAll('.dlgchip .areadot').length).toBe(2);
  const secondary = Array.from(
    summary.querySelectorAll('dd.secondary'),
    (el) => el.textContent ?? '',
  );
  expect(secondary.some((t) => t.includes('TRANSFERRED'))).toBe(true);
  expect(secondary.some((t) => t.includes('LATHE-ST-01'))).toBe(true);
  fireEvent.keyDown(dialog, { key: 'Escape' });
});

test('Add more quantity separates description, guidance, validation and reason hint', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  fireEvent.click(screen.getByRole('button', { name: /Add more quantity/ }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Add more quantity',
  });

  // Description: purpose and operational consequence.
  expect(dialog.querySelector('.sub')?.textContent).toContain(
    'Add physical quantity that was not received from another Area.',
  );
  // v15 entry recap: where the quantity is added and where it lands.
  expect(dialog.querySelector('.ss-recap')?.textContent).toContain(
    'Adding at Lathe',
  );
  // Quantity guidance: no MAX and no assumed default — an ℹ info
  // instruction (v15), with its marker.
  const guide = dialog.querySelector('.ss-guide.info')!;
  expect(guide.textContent).toContain('no MAX and no assumed default');
  expect(guide.querySelector('.gmark')?.textContent).toBe('ℹ');
  expect(dialog.querySelector('.keypad-max')).toBeNull();
  // Validation: a positive quantity is required (action kind).
  expect(dialog.querySelector('.ss-guide.action')?.textContent).toContain(
    'A positive quantity is required.',
  );
  // Reason requirement: explained at the field, not repeated verbatim.
  expect(dialog.querySelector('.ss-fieldhint')?.textContent).toContain(
    'later review',
  );
  fireEvent.keyDown(dialog, { key: 'Escape' });
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
  // v15 disconnected copy: the placeholder states that scanning is
  // disabled while the connection is lost.
  expect(input).toHaveAttribute(
    'placeholder',
    'Disconnected — scanning disabled',
  );

  // A scan attempt while write-blocked never writes — it surfaces the
  // blocked notice with the v15 wording.
  fireEvent.keyDown(input, { key: 'Enter' });
  const blocked = document.querySelector('.ss-toast.err')!;
  expect(blocked.textContent).toContain(
    'Disconnected — production actions are disabled',
  );
  expect(blocked.textContent).toContain(
    'Scans will not be recorded or queued until the connection to the PartFlow server is restored.',
  );

  failing = false;
  fireEvent(window, new Event('online'));

  await waitFor(() => expect(input).toBeEnabled());
  await waitFor(() => expect(input).toHaveFocus());
  expect(input).toHaveAttribute(
    'placeholder',
    'Scan PN / Worker / Machine barcode… (ENTER)',
  );
});

/* ============ Keyboard-wedge capture (main input must not lose scans) ============ */

function wedgeType(text: string) {
  for (const key of text) {
    fireEvent.keyDown(document.body, { key });
  }
}

test('a scan is captured while a non-input element has focus; the first character is preserved', async () => {
  const input = (await renderStation()) as HTMLInputElement;

  // Focus rests on the body — the wedge still reaches the main input.
  (document.activeElement as HTMLElement | null)?.blur?.();
  fireEvent.keyDown(document.body, { key: 'P' });
  // The FIRST character is never lost, and focus follows the scan.
  expect(input.value).toBe('P');
  expect(input).toHaveFocus();

  wedgeType('F:WORKER:88');
  expect(input.value).toBe('PF:WORKER:88');
  fireEvent.keyDown(document.body, { key: 'Enter' });

  // Submitted exactly once.
  expect(screen.getByText('Worker session: V. Tran')).toBeInTheDocument();
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
  expect(input.value).toBe('');

  // Enter with an empty input submits nothing new.
  fireEvent.keyDown(document.body, { key: 'Enter' });
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
});

test('scanning while the main input has focus keeps working (single submit)', async () => {
  await renderStation();

  scan('PF:WORKER:88');
  expect(screen.getByText('Worker session: V. Tran')).toBeInTheDocument();
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
});

test('wedge capture never interferes with dialogs, other fields, or buttons', async () => {
  const input = (await renderStation()) as HTMLInputElement;

  // While a Scan Station dialog is active, keystrokes are never routed
  // to the main barcode input.
  scan('PF:PN:0455-20-0118-03');
  const actions = await screen.findByRole('dialog', {
    name: 'Choose the action for this PN',
  });
  fireEvent.keyDown(actions, { key: 'X' });
  expect(input.value).toBe('');

  // Typing into another text field inside a dialog is left alone.
  fireEvent.click(screen.getByRole('button', { name: /Add more quantity/ }));
  const reason = screen.getByLabelText('Reason (required)');
  fireEvent.keyDown(reason, { key: '5' });
  expect(input.value).toBe('');
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

  // Enter on a focused button stays button activation — it never
  // submits the pending barcode value.
  fireEvent.change(input, { target: { value: 'PF:WORKER:88' } });
  const manual = screen.getByRole('button', { name: '⌨ Enter PN manually' });
  manual.focus();
  fireEvent.keyDown(manual, { key: 'Enter' });
  expect(screen.queryByText('Worker session: V. Tran')).toBeNull();
  expect(input.value).toBe('PF:WORKER:88');
});

/* ============ Touch-primary devices — main barcode input ============ */

test('a non-touch device leaves the main barcode input without inputMode', async () => {
  const input = await renderStation();
  expect(input).not.toHaveAttribute('inputmode');
});

test('a touch-primary device suppresses the soft keyboard on the main barcode input only', async () => {
  const original = window.matchMedia;
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        matches: query === '(pointer: coarse)' || query === '(hover: none)',
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  );
  Object.defineProperty(window.navigator, 'maxTouchPoints', {
    value: 5,
    configurable: true,
  });
  try {
    const input = await renderStation();
    // The native soft keyboard stays closed for the scanner-driven
    // main input…
    expect(input).toHaveAttribute('inputmode', 'none');

    // …while scanner/physical-keyboard input keeps working unchanged.
    scan('PF:PN:2027-60-8114-00');
    const dialog = await screen.findByRole('dialog', {
      name: 'Choose the action for this PN',
    });
    fireEvent.keyDown(dialog, { key: 'Escape' });

    // Manual PN entry is typed text: its input KEEPS the normal soft
    // keyboard (no inputMode suppression on ordinary text fields).
    fireEvent.click(
      screen.getByRole('button', { name: '⌨ Enter PN manually' }),
    );
    expect(screen.getByLabelText('Exact PartNumber')).not.toHaveAttribute(
      'inputmode',
    );
  } finally {
    window.matchMedia = original;
    Object.defineProperty(window.navigator, 'maxTouchPoints', {
      value: 0,
      configurable: true,
    });
  }
});

/* ============ Header structure, Operations chips, and totals ============ */

test('the header groups Area identity with the Worker Session and renders Operations as chips', async () => {
  await renderStation();

  // Explicit grid structure: identity group, totals, Worker pill are
  // the header's three cells — no spacer-based wrapping, so the
  // Worker Session can never wrap onto a row of its own.
  const head = document.querySelector('.ss-head')!;
  const children = Array.from(head.children, (el) => el.className);
  expect(children).toEqual(['ss-id', 'ss-stats', 'ss-pill']);
  const id = head.querySelector('.ss-id')!;
  expect(id.querySelector('.dept')?.textContent).toBe('Machine Shop');
  expect(id.querySelector('.area')?.textContent).toContain('Lathe');

  // Operations are light informational chips, not controls.
  const chips = Array.from(id.querySelectorAll('.op .opchip'));
  expect(chips.map((c) => c.textContent)).toEqual(['Turning']);
  for (const chip of chips) {
    expect(chip.tagName).not.toBe('BUTTON');
  }
});

test('header totals use semantic tones, include Done, and reconcile', async () => {
  await renderStation();

  const stats = new Map(
    Array.from(document.querySelectorAll('.ss-stats .stat'), (el) => [
      el.querySelector('.l')?.textContent,
      el.querySelector('.n'),
    ]),
  );
  expect([...stats.keys()]).toEqual([
    'Total PNs',
    'Total pcs',
    'Queued',
    'On machines',
    'Done',
    'Hot',
  ]);
  // Semantic tone classes: warning / info / success / error.
  expect(stats.get('Queued')?.classList.contains('q')).toBe(true);
  expect(stats.get('On machines')?.classList.contains('m')).toBe(true);
  expect(stats.get('Done')?.classList.contains('d')).toBe(true);
  expect(stats.get('Hot')?.classList.contains('h')).toBe(true);
  // The two plain totals stay non-status but visually distinct:
  // Total PNs keeps the primary neutral tone (no tone class), Total
  // pcs carries the secondary neutral tone `s` — never a status tone.
  expect(stats.get('Total PNs')?.className.trim()).toBe('n');
  expect(stats.get('Total pcs')?.classList.contains('s')).toBe(true);
  for (const tone of ['q', 'm', 'd', 'h']) {
    expect(stats.get('Total pcs')?.classList.contains(tone)).toBe(false);
  }
  // Quantity reconciliation: Total pcs = Queued + On machines + Done.
  const value = (label: string) => Number(stats.get(label)?.textContent);
  expect(value('Total pcs')).toBe(
    value('Queued') + value('On machines') + value('Done'),
  );
  expect(value('Total pcs')).toBe(12);
  expect(value('Done')).toBe(1);
});

test('the In this Area now card carries no statistics block', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary')!;
  expect(summary.querySelector('.mc-stats')).toBeNull();
  expect(summary.querySelectorAll('.stat').length).toBe(0);
  // The compact Area description stays in the card header block.
  expect(summary.querySelector('.abd-desc')?.textContent).toBe(
    'Turning cell · Lathe 1–4',
  );
});

test('Machine-card rows do not repeat the Machine name; summary rows keep context', async () => {
  await renderStation();

  // Lathe 2 card (index 1) holds 0455-20-0118-03 — no `Lathe 2`
  // context chip on the row; the card header already names it.
  const machineCards = document.querySelectorAll('.am-machines .abd-machine');
  const lathe2Row = machineCards[1].querySelector('.mc-list li')!;
  expect(lathe2Row.querySelector('.r1r .ctx')).toBeNull();

  // The same portion inside the Area summary keeps its context chip.
  const summary = document.querySelector('.abd-summary')!;
  const summaryChips = Array.from(
    summary.querySelectorAll('.mc-list .r1r .ctx'),
    (el) => el.textContent,
  );
  expect(summaryChips).toContain('Lathe 2');
});

/* ============ DONE — manual completion ============ */

test('manual DONE moves the selected quantity to Finished — ready to move', async () => {
  await renderStation();

  // Lathe 3 card (index 2) actively processes 3 pcs of 2027-60-8114-00.
  const machineCards = document.querySelectorAll('.am-machines .abd-machine');
  const lathe3 = machineCards[2] as HTMLElement;
  expect(lathe3.textContent).toContain('2027-60-8114-00');
  fireEvent.click(
    within(lathe3).getByRole('button', { name: 'Complete Area processing' }),
  );

  const dialog = await screen.findByRole('dialog', {
    name: 'Complete processing — DONE',
  });
  // MAX defaults to the quantity at the source position.
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
  expect(lastPnText()).toBe('—'); // nothing recorded yet

  // Dedicated confirmation view with the required summary fields.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('Complete Area processing');
  expect(dialog.textContent).toContain('Lathe 3');
  expect(dialog.textContent).toContain('Finished — ready to move');
  // The successful result carries the ok value tone (v15).
  expect(dialog.querySelector('.ss-confirm dd.tone-ok')?.textContent).toBe(
    'Finished — ready to move',
  );
  expect(dialog.textContent).toContain('AREA_COMPLETED');
  expect(lastPnText()).toBe('—');

  fireEvent.click(screen.getByRole('button', { name: 'Confirm DONE' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByText(/2027-60-8114-00 × 3 finished at Lathe/),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('2027-60-8114-00');

  // The quantity left the Machine card (current Machine cleared) …
  const lathe3After = document.querySelectorAll(
    '.am-machines .abd-machine',
  )[2] as HTMLElement;
  expect(lathe3After.textContent).not.toContain('2027-60-8114-00');
  expect(lathe3After.textContent).toContain('No production assigned');
  // … and appears under Finished — ready to move in the Area summary,
  // with the completing Machine kept only as completion context.
  const summary = document.querySelector('.abd-summary')!;
  expect(summary.textContent).toContain('Finished at Lathe 3 — ready to move');
  // Other quantity of the same PN is untouched (2 pcs stay queued).
  expect(summary.textContent).toContain('Awaiting Machine');
  // Header totals follow: Done 1+3, On machines 7−3, Queued unchanged.
  const stats = new Map(
    Array.from(document.querySelectorAll('.ss-stats .stat'), (el) => [
      el.querySelector('.l')?.textContent,
      Number(el.querySelector('.n')?.textContent),
    ]),
  );
  expect(stats.get('Done')).toBe(4);
  expect(stats.get('On machines')).toBe(4);
  expect(stats.get('Queued')).toBe(4);
  expect(stats.get('Total pcs')).toBe(12);
});

test('QUEUE returns quantity to the queue and never marks it DONE', async () => {
  await renderStation();

  // Lathe 2 card (index 1): 4 pcs of 0455-20-0118-03.
  const machineCards = document.querySelectorAll('.am-machines .abd-machine');
  const lathe2 = machineCards[1] as HTMLElement;
  fireEvent.click(
    within(lathe2).getByRole('button', { name: 'Return to Area queue' }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Return quantity to the Area queue',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm queue return' }));

  // The quantity is queued again — not finished.
  const lathe2After = document.querySelectorAll(
    '.am-machines .abd-machine',
  )[1] as HTMLElement;
  expect(lathe2After.textContent).toContain('No production assigned');
  const stats = new Map(
    Array.from(document.querySelectorAll('.ss-stats .stat'), (el) => [
      el.querySelector('.l')?.textContent,
      Number(el.querySelector('.n')?.textContent),
    ]),
  );
  expect(stats.get('Queued')).toBe(8);
  expect(stats.get('On machines')).toBe(3);
  expect(stats.get('Done')).toBe(1); // unchanged
  expect(lastPnText()).toBe('0455-20-0118-03');
  expect(document.querySelector('.ss-lastpn .d')?.textContent).toContain(
    'RELEASED_FROM_MACHINE',
  );
});

test('a direct-processing Area can also mark quantity DONE', async () => {
  await renderStation('EXT-ST-01');

  scan('PF:PN:142-260');
  const actions = await screen.findByRole('dialog', {
    name: 'Choose the action for this PN',
  });
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', {
      name: /Complete processing — DONE/,
    }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Complete processing — DONE',
  });
  // No Machine field for a no-Machine Area; MAX = processing quantity.
  expect(screen.getByLabelText('Quantity: 20')).toBeInTheDocument();
  // Finish only part of it: 5 pcs.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '5' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('Finished — ready to move');
  expect(dialog.textContent).not.toContain('Machine');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm DONE' }));

  const stats = new Map(
    Array.from(document.querySelectorAll('.ss-stats .stat'), (el) => [
      el.querySelector('.l')?.textContent,
      Number(el.querySelector('.n')?.textContent),
    ]),
  );
  expect(stats.get('Processing')).toBe(15);
  expect(stats.get('Done')).toBe(5);
  expect(stats.get('Total pcs')).toBe(20);
  expect(document.querySelector('.abd-summary')?.textContent).toContain(
    'Finished — ready to move',
  );
});

/* ============ Transfers complete source processing atomically ============ */

test('a transfer from actively processing quantity appends AREA_COMPLETED, then TRANSFERRED', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031'); // Mill (on Mill 1) + Deburr (finished)
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });

  // One atomic command: completion of source processing + transfer.
  expect(dialog.textContent).toContain('Source processing');
  expect(dialog.textContent).toContain('Completed by this transfer');
  // The source-processing deviation carries the warning value tone.
  expect(
    dialog.querySelector('.ss-confirm dd.tone-warn')?.textContent,
  ).toContain('Completed by this transfer');
  expect(dialog.textContent).toContain(
    'AREA_COMPLETED, then TRANSFERRED — one atomic operation',
  );
  fireEvent.keyDown(dialog, { key: 'Enter' });

  expect(lastPnText()).toBe('78-04-0031');
  expect(document.querySelector('.ss-lastpn .d')?.textContent).toContain(
    'AREA_COMPLETED + TRANSFERRED',
  );
  // The transferred quantity waits in this Area's queue.
  expect(document.querySelector('.abd-summary')?.textContent).toContain(
    '78-04-0031',
  );
});

test('a transfer from READY_TO_TRANSFER quantity appends only TRANSFERRED', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031');
  fireEvent.click(screen.getByRole('button', { name: /Deburr — 3 pcs/ }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });

  // No duplicate completion — the source quantity was already DONE.
  expect(dialog.textContent).not.toContain('AREA_COMPLETED');
  expect(dialog.textContent).not.toContain('Source processing');
  expect(dialog.textContent).toContain('TRANSFERRED');
  fireEvent.keyDown(dialog, { key: 'Enter' });

  expect(document.querySelector('.ss-lastpn .d')?.textContent).toContain(
    'TRANSFERRED',
  );
  expect(document.querySelector('.ss-lastpn .d')?.textContent).not.toContain(
    'AREA_COMPLETED',
  );
});

test('partial completion-and-transfer preserves the remaining source quantity', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031');
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });
  // Move only 2 of the 3 pcs.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  // The remaining 1 pc stays at the source in its existing state.
  scan('PF:PN:78-04-0031');
  const select = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  expect(select).toHaveTextContent('Mill — 1 pcs available');
  expect(select).toHaveTextContent('Deburr — 3 pcs available');
});

test('whole-command Undo reverses completion-plus-transfer together', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031');
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  enterThroughConfirmation();
  expect(document.querySelector('.abd-summary')?.textContent).toContain(
    '78-04-0031',
  );

  fireEvent.click(screen.getByRole('button', { name: '⟲ UNDO' }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Undo last PN operation?',
  });
  // The Undo summary names the COMPLETE command, not one arbitrary row.
  expect(dialog).toHaveTextContent('AREA_COMPLETED + TRANSFERRED');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Undo' }));

  // Both effects reverse together: the quantity left this Area again …
  expect(document.querySelector('.abd-summary')?.textContent).not.toContain(
    '78-04-0031',
  );
  // … and the source processing state is restored.
  scan('PF:PN:78-04-0031');
  const select = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  expect(select).toHaveTextContent('Mill — 3 pcs available');
});

/* ============ Standard Cancel label ============ */

test('wizard Cancel buttons use the standard `Cancel (Esc)` label', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Transfer into this Area',
  });
  const cancel = within(dialog as HTMLElement).getByRole('button', {
    name: 'Cancel (Esc)',
  });
  expect(cancel.textContent).toBe('Cancel (Esc)'); // no long suffixes
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // The manual PN entry dialog shares the standard wizard buttons
  // (v15): the same Cancel label plus a primary `Look up PN` action.
  fireEvent.click(screen.getByRole('button', { name: '⌨ Enter PN manually' }));
  const manual = await screen.findByRole('dialog', {
    name: 'Manual PN entry — explicit fallback',
  });
  expect(
    within(manual as HTMLElement).getByRole('button', {
      name: 'Cancel (Esc)',
    }).textContent,
  ).toBe('Cancel (Esc)');
  expect(
    within(manual as HTMLElement).getByRole('button', { name: 'Look up PN' }),
  ).toHaveClass('primary');
  fireEvent.keyDown(manual, { key: 'Escape' });
});
