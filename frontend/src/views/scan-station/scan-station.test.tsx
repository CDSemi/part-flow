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
  expect(lathe).toHaveTextContent('4 Machines · Queue and assignment enabled');
  const deburr = screen
    .getByRole('button', { name: 'Open DEBURR-ST-01' })
    .closest('.ss-stationcard') as HTMLElement;
  expect(deburr).toHaveTextContent(
    'Direct Area processing · No Machine assignment',
  );
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
    await screen.findByText(/Scan Station “GHOST-ST-99” is unavailable/),
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
  const manualInput = screen.getByLabelText('Part Number');
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
  // Both direct-processing groups render: actively processing quantity
  // under `In processing`, finished (READY_TO_TRANSFER) quantity under
  // `Finished — ready to move` — and never any queue wording.
  const summary = document.querySelector('.abd-summary');
  expect(summary?.textContent).toContain('In processing');
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
    'Scan Part Number, Worker, or Machine barcode · Press Enter',
  );

  // Manual PN entry occupies the scan-row secondary position; the old
  // separate manual-entry row is gone (the hint remains as text only).
  const manual = screen.getByRole('button', { name: '⌨ Enter PN manually' });
  expect(manual.closest('.ss-scanrow')).not.toBeNull();
  expect(document.querySelector('.ss-manual')).toBeNull();
  expect(document.querySelector('.ss-manualcap')).not.toBeNull();

  // The demo-barcodes hint is the shared dev-only notice (v16) — the
  // old .ss-hint block is gone, the barcode values stay code chips,
  // and the complete copy lives inside ONE .dev-notice-content flow.
  expect(document.querySelector('.ss-hint')).toBeNull();
  const hint = document.querySelector('.dev-notice');
  expect(hint?.textContent).toContain('Demo barcodes (development build only)');
  expect(hint?.querySelectorAll('code').length).toBe(6);
  const content = hint?.querySelector('.dev-notice-content');
  expect(content?.textContent).toContain('Demo barcodes');
  expect(content?.querySelectorAll('code').length).toBe(6);

  // Fixed DOM order (visual = keyboard = screen reader — no CSS
  // reordering): scan row, manual fallback explanation, DEV notice,
  // then the Last scanned PN section with its label OUTSIDE the block.
  const manualCap = document.querySelector('.ss-manualcap')!;
  const scanRow = document.querySelector('.ss-scanrow')!;
  const label = document.querySelector('.ss-lastpnlabel')!;
  const lastPn = document.querySelector('.ss-lastpn')!;
  const follows = (a: Element, b: Element) =>
    Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(follows(scanRow, manualCap)).toBe(true);
  expect(follows(manualCap, hint!)).toBe(true);
  expect(follows(hint!, label)).toBe(true);
  expect(follows(label, lastPn)).toBe(true);
  expect(label.textContent).toBe('Last Action');
  expect(lastPn.contains(label)).toBe(false);
  expect(lastPn.textContent).not.toContain('Last Action');

  // Two explicit regions, no separator element: the information
  // region fills the remaining space; the WHOLE right region is the
  // Undo button itself (divided by its own border-left), flush with
  // the block's right edge.
  expect(lastPn.querySelector('.ss-undosep')).toBeNull();
  expect(lastPn.textContent).not.toContain('|');
  const info = lastPn.querySelector('.ss-lastpninfo')!;
  expect(info.querySelector('.p')).not.toBeNull();
  expect(info.querySelector('.d')).not.toBeNull();
  const undoRegion = lastPn.querySelector('button.ss-undo.zone-action')!;
  expect(lastPn.lastElementChild).toBe(undoRegion);
  expect(undoRegion.textContent).toContain('⟲ UNDO');

  // Worker session window: `from <badge-scan time> to <shift end>`.
  expect(document.querySelector('.ss-pill .sub')?.textContent).toMatch(
    /^from \d{2}:\d{2} to 18:00$/,
  );

  // No permanently reserved feedback block below Last scanned PN.
  expect(document.querySelector('.ss .ss-feedback')).toBeNull();
});

test('scan feedback floats as a single closable notification', async () => {
  await renderStation();

  scan('NOT-A-PARTFLOW-BARCODE');
  const toast = document.querySelector('.ss-toast.err');
  expect(toast).not.toBeNull();
  expect(toast).toHaveAttribute('role', 'alert');
  expect(toast?.textContent).toContain('Barcode not recognized');

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

test('Machine Area: In this Area now has no row actions; Machine-card rows offer DONE and QUEUE', async () => {
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

/* ============ Direct-processing Areas — row-level DONE ============ */

test('direct-processing Area: actively processing rows carry DONE — and only those rows', async () => {
  await renderStation('DEBURR-ST-01');

  const summary = document.querySelector('.abd-summary')! as HTMLElement;
  // Exactly the actively processing row carries the single DONE
  // action, in the shared separated action rail with the shared
  // presentation: success tone, icon above the label, accessible name.
  const done = within(summary).getAllByRole('button', {
    name: 'Complete Area processing',
  });
  expect(done.length).toBe(1);
  expect(done[0]).toHaveTextContent('DONE');
  expect(done[0].classList.contains('done')).toBe(true);
  expect(done[0].querySelector('.ric')).not.toBeNull();
  expect(done[0].closest('.actcell')).not.toBeNull();
  const row = done[0].closest('li')!;
  expect(row.classList.contains('has-action')).toBe(true);
  expect(row.querySelector('.rowmain')?.textContent).toContain('81-1042');
  expect(row.querySelector('.rowmain')?.textContent).toContain('In processing');
  // No QUEUE anywhere — a direct-processing Area has no Machine queue
  // to return quantity to.
  expect(
    within(summary).queryByRole('button', { name: 'Return to Area queue' }),
  ).toBeNull();
  // The finished (READY_TO_TRANSFER) row never carries the action.
  const finishedRow = Array.from(summary.querySelectorAll('.mc-list li')).find(
    (li) => li.textContent?.includes('Finished — ready to move'),
  )! as HTMLElement;
  expect(finishedRow.classList.contains('has-action')).toBe(false);
  expect(within(finishedRow).queryByRole('button')).toBeNull();
});

test('row DONE opens the existing DONE wizard without a Machine field; partial DONE moves only the confirmed quantity', async () => {
  await renderStation('DEBURR-ST-01');

  fireEvent.click(
    screen.getByRole('button', { name: 'Complete Area processing' }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  // The existing manual DONE wizard: MAX defaults to the actively
  // processing quantity of the selected row; the direct-processing
  // guidance wording; no Machine field or wording anywhere.
  expect(screen.getByLabelText('Quantity: 6')).toBeInTheDocument();
  expect(dialog.textContent).toContain('6 pcs in process');
  expect(dialog.textContent).not.toContain('Machine');

  // Partial DONE: finish only 2 of the 6 pcs.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  // Dedicated confirmation view — the fixed rows WITHOUT a Machine row.
  const labels = Array.from(
    dialog.querySelectorAll('.ss-confirm dt'),
    (el) => el.textContent,
  );
  expect(labels).toEqual([
    'Action',
    'PN',
    'Quantity',
    'Area',
    'Result',
    'Worker',
    'Scan Station',
    'Recorded event',
  ]);
  expect(dialog.textContent).toContain('Complete Area processing');
  expect(dialog.textContent).toContain('Finished — ready to move');
  expect(dialog.textContent).toContain('AREA_COMPLETED');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm completion' }));

  // Only the confirmed 2 pcs moved to `Finished — ready to move`; the
  // remaining 4 pcs keep processing and keep their DONE action. The
  // current Area never changes — both portions stay in this card.
  const summary = document.querySelector('.abd-summary')! as HTMLElement;
  const rows = Array.from(summary.querySelectorAll('.mc-list li'));
  const processing = rows.find(
    (li) =>
      li.textContent?.includes('81-1042') &&
      li.textContent?.includes('In processing'),
  )! as HTMLElement;
  expect(processing.querySelector('.qtyline')?.textContent).toContain('4');
  expect(
    within(processing).getByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeInTheDocument();
  const finished = rows.find(
    (li) =>
      li.textContent?.includes('81-1042') &&
      li.textContent?.includes('Finished — ready to move'),
  )! as HTMLElement;
  expect(finished.querySelector('.qtyline')?.textContent).toContain('2');
  expect(within(finished).queryByRole('button')).toBeNull();
  // The other PN's portions are untouched.
  expect(summary.textContent).toContain('78-04-0031');
  // Header totals reconcile after the partial DONE: 4 processing,
  // 3 + 2 finished.
  const stats = new Map(
    Array.from(document.querySelectorAll('.ss-stats .stat'), (el) => [
      el.querySelector('.l')?.textContent,
      el.querySelector('.n')?.textContent,
    ]),
  );
  expect(stats.get('Processing')).toBe('4');
  expect(stats.get('Done')).toBe('5');

  // Last Scanned PN and Undo follow the manual DONE behavior.
  expect(lastPnText()).toBe('81-1042');
  expect(document.querySelector('.ss-lastpn .d')?.textContent).toContain(
    'AREA_COMPLETED',
  );
  expect(screen.getByRole('button', { name: '⟲ UNDO' })).toBeEnabled();
});

test('a fully completed row leaves In processing and shows no DONE anywhere', async () => {
  await renderStation('DEBURR-ST-01');

  fireEvent.click(
    screen.getByRole('button', { name: 'Complete Area processing' }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  // MAX (6) is the default — Enter advances, Enter confirms.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  const summary = document.querySelector('.abd-summary')! as HTMLElement;
  expect(summary.textContent).not.toContain('In processing');
  expect(
    within(summary).queryByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeNull();
  // The whole quantity waits on the finished rack.
  const finished = Array.from(summary.querySelectorAll('.mc-list li')).find(
    (li) =>
      li.textContent?.includes('81-1042') &&
      li.textContent?.includes('Finished — ready to move'),
  )! as HTMLElement;
  expect(finished.querySelector('.qtyline')?.textContent).toContain('6');
});

test('disconnected: the direct-processing DONE stays in the rail but is disabled and opens nothing', async () => {
  failing = true;
  window.history.replaceState({}, '', '/scan-station/DEBURR-ST-01');
  render(<App />);
  await screen.findByText('OFFLINE');

  const done = await screen.findByRole('button', {
    name: 'Complete Area processing',
  });
  // Disabled in place — the action rail keeps its layout, and no
  // workflow that could record a false state can open.
  expect(done).toBeDisabled();
  expect(done.closest('.actcell')).not.toBeNull();
  fireEvent.click(done);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

/* ============ No Machine Session — one-shot Machine scan ============ */

test('a Machine scan opens the Assign to Machine dialog and leaves nothing armed', async () => {
  await renderStation();

  scan('PF:MACHINE:L2');
  const dialog = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  expect(dialog).toHaveTextContent('This assignment applies once');

  // Cancel: no write, no sticky Machine context anywhere.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
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
  expect(dialog.textContent).toContain('closes after confirmation');
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
  expect(dialog.textContent).toContain(
    'The full queued quantity is selected by default',
  );
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
      name: 'Select an action',
    }),
  ).toBeInTheDocument();
});

test('Step 1 barcode selection: Machine and queued PN scans select; invalid scans error inline', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  const scanField = screen.getByLabelText(
    'Scan machine or queued part barcode',
  );
  expect(scanField).toHaveAttribute(
    'placeholder',
    'Scan machine or queued part barcode… (ENTER)',
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
  expect(dialog.textContent).toContain(
    'Scan a Machine in this Area or a Part Number currently waiting in the Area queue. Your current selections were kept.',
  );
  // Selection errors share the Guidance error presentation (v16).
  expect(dialog.querySelector('.ss-guide.error')?.textContent).toContain(
    'Your current selections were kept',
  );
  expect(dialog.querySelector('.ss-scannote')).toBeNull();
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
    screen.getByText('Lathe 4 is unavailable for production'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

/* ============ PN scan resolution ============ */

test('a PN with quantity in the Area opens the one-shot action dialog with valid choices only', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  const dialog = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  // Queued quantity exists → assign choice; quantity at Cut → receive.
  expect(dialog).toHaveTextContent('Assign queued quantity to a Machine');
  expect(dialog).toHaveTextContent('Receive more quantity from another Area');
  expect(dialog).toHaveTextContent('Add more quantity');
  expect(dialog).toHaveTextContent('Scrap damaged quantity');
  // No repair source exists for this PN → the intent is not offered.
  expect(dialog).not.toHaveTextContent('Return quantity for repair');

  // Cancel abandons with no write and never touches Last Scanned PN.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
});

test('a PN elsewhere with one source goes to quantity, then a confirmation view', async () => {
  await renderStation();

  scan('PF:PN:118-052'); // only at Manual (4 pcs)
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(dialog).toHaveTextContent('Transfer Manual → Lathe');
  // The Work Order context is no longer one raw string: `WO` stays a
  // plain label, the WO Number carries the shared .rval emphasis, and
  // the Operation renders as the shared entity chip.
  const woLine = Array.from(dialog.querySelectorAll('.ss-recapline')).find(
    (el) => el.textContent?.startsWith('WO '),
  )!;
  expect(woLine.querySelector('.rval')?.textContent).toBe('007011');
  expect(woLine.querySelector('.dlgchip')?.textContent).toBe('Manual work');
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
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
});

test('multiple sources require explicit selection — never combined silently', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031'); // Mill 3 + Deburr 3
  const dialog = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  expect(dialog).toHaveTextContent('Select one source to continue');
  expect(dialog).toHaveTextContent('Mill — 3 pcs available');
  expect(dialog).toHaveTextContent('Deburr — 3 pcs available');

  fireEvent.click(screen.getByRole('button', { name: /Deburr — 3 pcs/ }));
  const qtyDialog = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(qtyDialog).toHaveTextContent('Transfer Deburr → Lathe');
  expect(screen.getByLabelText('Quantity: 3')).toBeInTheDocument();
});

/* ============ Receive wizard (no active WO Demand) ============ */

test('an unknown PN opens the three-step receive wizard with editable defaults', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive Quantity',
  });
  // Operator-facing guidance: what was scanned and what confirmation
  // does — never internal record/persistence wording.
  expect(dialog).toHaveTextContent('New Part Number');
  expect(dialog).toHaveTextContent(
    'it will be registered when you confirm the receipt',
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

  // Step 2 — compact two-line recap (v16): the SHARED Request Type and
  // Route Mode chips on one line (the route name lives inside the
  // Route Mode chip), the WO/due context with emphasized values on
  // the other, then the instruction guidance above the quantity input.
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  const recap = dialog.querySelector('.ss-recap')!;
  expect(recap.querySelector('.typechip')?.textContent).toBe('MODIFY');
  expect(recap.querySelector('.routechip')?.textContent).toBe(
    'FLOATING — actual trace',
  );
  // Fixed recap order with standalone `·` separators BETWEEN the
  // chips (never inside chip content): TypeChip · RouteModeChip ·
  // Operation chip.
  const line1 = recap.querySelector('.ss-recapline')!;
  expect(line1.textContent).toMatch(
    /^MODIFY · FLOATING — actual trace · Lathe/,
  );
  const chipOrder = Array.from(
    line1.querySelectorAll('.typechip, .routechip, .dlgchip'),
    (el) => el.className.split(' ')[0],
  );
  expect(chipOrder).toEqual(['typechip', 'routechip', 'dlgchip']);
  expect(recap.textContent).toContain('WO —');
  expect(recap.textContent).toContain('Due: —');
  // Value emphasis is text-only: the WO Number and due values render
  // in the .rval emphasis, labels keep normal weight.
  const rvals = Array.from(
    recap.querySelectorAll('.rval'),
    (el) => el.textContent,
  );
  expect(rvals).toEqual(['—', '—']);
  expect(dialog.textContent).toContain(
    'Enter the physical quantity received. No default quantity is assumed.',
  );
  fireEvent.keyDown(dialog, { key: '6' });

  // Step 3 — structured confirmation; only Confirm receipt records.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('Receive Quantity');
  expect(dialog.textContent).toContain('RECEIVED');
  expect(dialog.textContent).toContain(
    'Creates an internal Work Order without an external number',
  );
  // The confirmation carries the shared chips too, and no separate
  // Planned Route row (the chip holds the route name).
  const summary = dialog.querySelector('.ss-confirm')!;
  expect(summary.querySelector('.typechip')?.textContent).toBe('MODIFY');
  expect(summary.querySelector('.routechip')?.textContent).toBe(
    'FLOATING — actual trace',
  );
  expect(summary.textContent).not.toContain('Planned Route');
  expect(lastPnText()).toBe('—');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm receipt' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByText(/NEW-PART-01 × 6 received into Lathe queue/),
  ).toBeInTheDocument();
  // Internal Work Order without an external number — no TMP number.
  const detail = document.querySelector('.ss-toast .t2')?.textContent ?? '';
  expect(detail).toContain(
    'Receipt recorded. The quantity is now waiting in the Area queue.',
  );
  expect(detail).not.toMatch(/TMP-/);
});

test('the receive settings step separates PN identity, defaults recap, and confirmation guidance', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive Quantity',
  });

  // The description carries PN identity only; the former
  // default-selection recap sentences are gone — the defaults are
  // visible directly in the fields, so the settings page has NO
  // recap block at all.
  expect(dialog.querySelector('.sub')?.textContent).toContain(
    'New Part Number. Verify it carefully',
  );
  expect(dialog.querySelector('.ss-recap')).toBeNull();
  expect(dialog.textContent).not.toContain(
    'MODIFY and FLOATING are selected by default',
  );
  expect(dialog.textContent).not.toContain(
    'The received date is recorded from this scan',
  );
  // The optional due date is marked inside its label with the shared
  // quiet optional element.
  const dueLabel = Array.from(dialog.querySelectorAll('label')).find((l) =>
    l.textContent?.startsWith('Due date'),
  )!;
  expect(dueLabel.querySelector('.field-optional')?.textContent).toBe(
    '(optional)',
  );

  // ℹ info guidance: nothing is recorded before the final step.
  const guide = dialog.querySelector('.ss-guide.info')!;
  expect(guide.querySelector('.gmark')?.textContent).toBe('ℹ');
  expect(guide.textContent).toContain(
    'Nothing is recorded until the final confirmation.',
  );
  // v15: the marker-less `neutral` guidance kind no longer exists.
  expect(dialog.querySelector('.ss-guide.neutral')).toBeNull();
  fireEvent.keyDown(dialog, { key: 'Escape' });
});

test('receive Back preserves settings and quantity; Cancel records nothing', async () => {
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
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
});

test('PN identity is case-insensitive and keeps the first-entered casing', async () => {
  await renderStation();

  scan('PF:PN:abc-part');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive Quantity',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.keyDown(dialog, { key: '1' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm receipt' }));
  expect(screen.getByText(/abc-part × 1/)).toBeInTheDocument();

  // Scanning the same PN in a different casing resolves to the SAME
  // PartNumber — the received quantity is now in the Area, so the PN
  // action dialog opens — and shows the preserved original casing.
  scan('PF:PN:ABC-PART');
  const dialog2 = await screen.findByRole('dialog', {
    name: 'Select an action',
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
    'The added quantity is now waiting in the Area queue',
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
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('—');
});

/* ============ Repair ============ */

test('the Repair intent is explicit: source, quantity, reason, then a Repair confirmation', async () => {
  await renderStation();

  scan('PF:PN:0455-20-0118-03');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  // A repair source exists for this PN → the intent is offered, but
  // never auto-selected.
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', {
      name: /Return quantity for repair/,
    }),
  );

  const dialog = await screen.findByRole('dialog', {
    name: 'Return quantity for repair',
  });
  // Operator wording — the intent is explicit, never inferred.
  expect(dialog).toHaveTextContent('This moves existing quantity');
  expect(dialog).toHaveTextContent('it does not create additional quantity');

  // v15 entry recap names the repair destination.
  expect(dialog.querySelector('.ss-recap')?.textContent).toContain(
    'Repair destination: Lathe',
  );

  // The single known source is preselected; quantity defaults to MAX.
  // The MAX/default statement is an ℹ instruction (v15), not a warning.
  expect(screen.getByLabelText('Quantity: 4')).toBeInTheDocument();
  const maxGuide = dialog.querySelector('.ss-guide.info')!;
  expect(maxGuide.textContent).toContain('Up to 4 pcs are available');
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
  expect(screen.getByText(/is not a valid scrap barcode/)).toBeInTheDocument();
  // The rejection renders in the shared Guidance error style (v16).
  expect(dialog.querySelector('.ss-guide.error')?.textContent).toContain(
    'is not a valid scrap barcode',
  );
  expect(dialog.querySelector('.ss-scrapcount .cnt')?.textContent).toBe('3');

  // The count can be corrected before confirmation.
  fireEvent.click(screen.getByRole('button', { name: 'Remove one' }));
  expect(dialog.querySelector('.ss-scrapcount .cnt')?.textContent).toBe('2');

  // Available / pending / remaining stay visible while counting — a
  // plain ℹ status line (v15), not a warning.
  const statusLine = dialog.querySelector('.ss-guide.info')!;
  expect(statusLine.textContent).toContain('Available at Lathe');
  expect(statusLine.querySelector('.gmark')?.textContent).toBe('ℹ');

  // Next stays blocked until the common reason exists.
  const next = screen.getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Scrap reason (required)'), {
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
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
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
  fireEvent.change(screen.getByLabelText('Scrap reason (required)'), {
    target: { value: 'gouged face' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm scrap' }));

  expect(
    screen.getByText('Scrapped — 2027-60-8114-00 × 1 at Lathe'),
  ).toBeInTheDocument();
  expect(document.querySelector('.ss-toast .t2')?.textContent).toContain(
    'The scrap quantity and reason were recorded',
  );
});

test('scrap instructions describe the workflow without the SCRAPPED event name', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  // The action-choice explanation uses operator wording (v15) — the
  // canonical event name never appears in instructions.
  const choice = within(actions as HTMLElement).getByRole('button', {
    name: /Scrap damaged quantity/,
  });
  expect(choice.textContent).toContain(
    'Scan PF:SCRAP once for each damaged piece, then enter one reason for the total. Nothing is recorded until confirmation.',
  );
  expect(choice.textContent).not.toContain('SCRAPPED');

  fireEvent.click(choice);
  const dialog = await screen.findByRole('dialog', {
    name: 'Scrap damaged quantity',
  });
  expect(dialog.querySelector('.sub')?.textContent).toContain(
    'Scan PF:SCRAP once for each damaged piece, then enter one reason for the total. Nothing is recorded until confirmation.',
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
    screen.getByText(/Scrap barcode cannot be used here/),
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
    name: 'Return unfinished quantity to queue',
  });
  expect(dialog).toHaveTextContent(
    'Enter a lower quantity to return only part of them',
  );

  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.querySelector('.ss-confirm')).not.toBeNull();
  expect(dialog.textContent).toContain('RELEASED_FROM_MACHINE');
  expect(dialog.textContent).toContain('Remaining on Machine');
  expect(lastPnText()).toBe('—');

  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm return to queue' }),
  );
  expect(
    screen.getByText(/× \d+ returned to the Lathe queue/),
  ).toBeInTheDocument();
});

/* ============ Worker scans and Last Scanned PN ============ */

test('a Worker scan switches the session and never replaces the Last Scanned PN', async () => {
  await renderStation();

  // Complete one PN operation first (quantity → confirmation → confirm).
  scan('PF:PN:118-052');
  enterThroughConfirmation();
  expect(lastPnText()).toBe('118-052');

  scan('PF:WORKER:88');
  expect(screen.getByText('Worker signed in: V. Tran')).toBeInTheDocument();
  expect(lastPnText()).toBe('118-052');

  // The badge scan opens a NEW session window: the pill shows the new
  // Worker with `from <badge-scan time> to <shift end>`.
  expect(document.querySelector('.ss-pill .val')?.textContent).toContain(
    'V. Tran',
  );
  expect(document.querySelector('.ss-pill .sub')?.textContent).toMatch(
    /^from \d{2}:\d{2} to 18:00$/,
  );
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
    name: 'Reverse the last Part Number action?',
  });
  expect(dialog).toHaveTextContent('78-04-0031');
  expect(dialog).toHaveTextContent('TRANSFERRED');
  expect(dialog).toHaveTextContent('Mill → Lathe');
  expect(dialog).toHaveTextContent('Result after reversal');
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
  fireEvent.click(screen.getByRole('button', { name: 'Confirm reversal' }));
  expect(
    screen.getByText('Last action reversed — 78-04-0031'),
  ).toBeInTheDocument();
  expect(lastPnText()).toBe('118-052');

  // Undo the remaining operation; afterwards nothing is eligible.
  fireEvent.click(undoButton());
  fireEvent.click(screen.getByRole('button', { name: 'Confirm reversal' }));
  expect(lastPnText()).toBe('—');
  expect(undoButton()).toBeDisabled();
});

/* ============ Quantity keypad keyboard behavior ============ */

test('the quantity step opens focused and maps physical keys centrally', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
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
    name: 'Receive from another Area',
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
    name: 'Receive from another Area',
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
    name: 'Receive from another Area',
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
    'Add physical quantity found at this Area that was not transferred from another Area.',
  );
  // v15 entry recap: where the quantity is added and where it lands.
  expect(dialog.querySelector('.ss-recap')?.textContent).toContain(
    'Adding at Lathe',
  );
  // Quantity guidance: no MAX and no assumed default — an ℹ info
  // instruction (v15), with its marker.
  const guide = dialog.querySelector('.ss-guide.info')!;
  expect(guide.textContent).toContain('No default quantity is provided');
  expect(guide.querySelector('.gmark')?.textContent).toBe('ℹ');
  expect(dialog.querySelector('.keypad-max')).toBeNull();
  // Validation: a positive quantity is required (action kind).
  expect(dialog.querySelector('.ss-guide.action')?.textContent).toContain(
    'A positive quantity is required.',
  );
  // Reason requirement: explained at the field, not repeated verbatim.
  expect(dialog.querySelector('.ss-fieldhint')?.textContent).toContain(
    'adjustment history',
  );
  fireEvent.keyDown(dialog, { key: 'Escape' });
});

test('MAX exists for transfers; Escape always cancels with no write', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const dialog = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
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
  expect(
    screen.getByText('Cancelled. No changes were recorded.'),
  ).toBeInTheDocument();
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
  expect(blocked.textContent).toContain('Connection lost — scanning is paused');
  expect(blocked.textContent).toContain(
    'Reconnect to PartFlow server before continuing. No scans or production updates will be recorded while offline.',
  );

  failing = false;
  fireEvent(window, new Event('online'));

  await waitFor(() => expect(input).toBeEnabled());
  await waitFor(() => expect(input).toHaveFocus());
  expect(input).toHaveAttribute(
    'placeholder',
    'Scan Part Number, Worker, or Machine barcode · Press Enter',
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
  expect(screen.getByText('Worker signed in: V. Tran')).toBeInTheDocument();
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
  expect(input.value).toBe('');

  // Enter with an empty input submits nothing new.
  fireEvent.keyDown(document.body, { key: 'Enter' });
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
});

test('scanning while the main input has focus keeps working (single submit)', async () => {
  await renderStation();

  scan('PF:WORKER:88');
  expect(screen.getByText('Worker signed in: V. Tran')).toBeInTheDocument();
  expect(document.querySelectorAll('.ss-toast').length).toBe(1);
});

test('wedge capture never interferes with dialogs, other fields, or buttons', async () => {
  const input = (await renderStation()) as HTMLInputElement;

  // While a Scan Station dialog is active, keystrokes are never routed
  // to the main barcode input.
  scan('PF:PN:0455-20-0118-03');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
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
  expect(screen.queryByText('Worker signed in: V. Tran')).toBeNull();
  expect(input.value).toBe('PF:WORKER:88');
});

/* ============ Scan → dialog focus and Enter routing (v16) ============ */

test('a scan that opens a dialog leaves focus inside the dialog after the refocus window', async () => {
  await renderStation();
  vi.useFakeTimers();

  // A Worker scan queues the delayed input refocus; the PN scan opens
  // its dialog before that timer fires. After flushing the delayed
  // refocus window, focus must sit INSIDE the dialog — a pending
  // refocus never pulls focus back out of an open dialog.
  scan('PF:WORKER:88');
  scan('PF:PN:2027-60-8114-00');
  const dialog = activeDialog();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(screen.getByRole('dialog')).toBe(dialog);
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(screen.getByLabelText('Scan barcode')).not.toHaveFocus();
});

test('a Machine scan opens Assign to Machine with focus on the in-dialog scan field', async () => {
  await renderStation();
  vi.useFakeTimers();

  scan('PF:MACHINE:L2');
  const dialog = activeDialog();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(
    screen.getByLabelText('Scan machine or queued part barcode'),
  ).toHaveFocus();
});

test('Enter acts inside the open dialog and Escape closes it — the main input never swallows them', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const scanField = screen.getByLabelText(
    'Scan machine or queued part barcode',
  );
  fireEvent.change(scanField, { target: { value: 'PF:PN:2027-60-8114-00' } });
  fireEvent.keyDown(scanField, { key: 'Enter' });
  // Machine + PN selected; the empty-input Enter advances to quantity.
  fireEvent.keyDown(scanField, { key: 'Enter' });
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();

  // Escape closes the whole wizard — no write.
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(lastPnText()).toBe('—');
});

test('focus returns to the main barcode input after the dialog closes', async () => {
  const input = await renderStation();

  scan('PF:PN:2027-60-8114-00');
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  await waitFor(() => expect(input).toHaveFocus());
});

test('wedge capture keeps working after a dialog cycle: a full barcode lands intact', async () => {
  const input = (await renderStation()) as HTMLInputElement;

  // Open and cancel a dialog first — the capture must re-attach.
  scan('PF:PN:2027-60-8114-00');
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();

  // No dialog open, the main input NOT focused: every character of
  // the wedge burst is captured, then Enter processes the scan once.
  (document.activeElement as HTMLElement | null)?.blur?.();
  wedgeType('PF:WORKER:88');
  expect(input.value).toBe('PF:WORKER:88');
  fireEvent.keyDown(document.body, { key: 'Enter' });
  expect(screen.getByText('Worker signed in: V. Tran')).toBeInTheDocument();
  expect(input.value).toBe('');
});

test('Assign to Machine Enter rules: filled scans once, empty advances only when complete', async () => {
  await renderStation();

  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  const scanField = screen.getByLabelText(
    'Scan machine or queued part barcode',
  ) as HTMLInputElement;

  // Empty input + incomplete pair (no PN yet): Enter does nothing.
  fireEvent.keyDown(scanField, { key: 'Enter' });
  expect(dialog.querySelector('.qtydisplay')).toBeNull();

  // Filled input: Enter is ONE selection scan — it completes the pair
  // but never also advances from the same keypress.
  fireEvent.change(scanField, { target: { value: 'PF:PN:2027-60-8114-00' } });
  fireEvent.keyDown(scanField, { key: 'Enter' });
  expect(scanField.value).toBe('');
  expect(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  ).toHaveClass('sel');
  expect(dialog.querySelector('.qtydisplay')).toBeNull(); // still Step 1

  // Empty input + complete pair: Enter advances to the quantity step.
  fireEvent.keyDown(scanField, { key: 'Enter' });
  expect(screen.getByLabelText('Quantity: 2')).toBeInTheDocument();
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
      name: 'Select an action',
    });
    fireEvent.keyDown(dialog, { key: 'Escape' });

    // Manual PN entry is typed text: its input KEEPS the normal soft
    // keyboard (no inputMode suppression on ordinary text fields).
    fireEvent.click(
      screen.getByRole('button', { name: '⌨ Enter PN manually' }),
    );
    expect(screen.getByLabelText('Part Number')).not.toHaveAttribute(
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

test('the Area totals drop to the second row only when the measured row cannot fit', async () => {
  await renderStation();
  const head = document.querySelector('.ss-head') as HTMLElement;

  // jsdom performs no layout: install explicit widths — the header's
  // available width against the three cells' natural single-row
  // widths — and let the resize listener re-run the fit measurement.
  const setWidths = (available: number, cells: number[]) => {
    Object.defineProperty(head, 'clientWidth', {
      value: available,
      configurable: true,
    });
    Array.from(head.children).forEach((cell, i) => {
      (cell as HTMLElement).getBoundingClientRect = () =>
        ({ width: cells[i] }) as DOMRect;
    });
  };

  // Zero-layout default (mount): the totals stay on the main row.
  expect(head.classList.contains('wrapped')).toBe(false);

  // Plenty of space: identity, totals, and Worker Session share the
  // main row — no premature wrap at any hard-coded width.
  setWidths(1000, [300, 400, 200]);
  fireEvent(window, new Event('resize'));
  expect(head.classList.contains('wrapped')).toBe(false);

  // The same content in a genuinely insufficient header: the totals
  // move to the full-width second row.
  setWidths(880, [300, 400, 200]);
  fireEvent(window, new Event('resize'));
  expect(head.classList.contains('wrapped')).toBe(true);

  // Space returns: the totals rejoin the main row (no sticky state).
  setWidths(1200, [300, 400, 200]);
  fireEvent(window, new Event('resize'));
  expect(head.classList.contains('wrapped')).toBe(false);

  // The probe class never leaks out of the synchronous measurement.
  expect(head.classList.contains('measuring')).toBe(false);
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
  // Total PNs carries the primary text neutral (`pn`) — never a status
  // tone; Total pcs keeps the secondary muted neutral with no tone
  // class at all.
  expect(stats.get('Total PNs')?.className.trim()).toBe('n pn');
  expect(stats.get('Total pcs')?.className.trim()).toBe('n');
  for (const tone of ['s', 'q', 'm', 'd', 'h']) {
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
    name: 'Complete Area processing',
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

  fireEvent.click(screen.getByRole('button', { name: 'Confirm completion' }));
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
    name: 'Return unfinished quantity to queue',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm return to queue' }),
  );

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

/* ============ Machine state follows the session assignments ============ */

// Running/idle are DERIVED from the quantity actively assigned on each
// Machine in the session-local mock state — queue and finished never
// count, maintenance stays an explicit override — and `stateChangedAt`
// restamps only when a confirmed command actually flips a Machine
// between Idle and Running (the new state's age starts at the flip,
// never at the old state's timestamp).

const machineCard = (index: number) =>
  document.querySelectorAll('.am-machines .abd-machine')[index] as HTMLElement;

const machineStat = (index: number) =>
  machineCard(index).querySelector('.mstat')?.textContent ?? '';

test('assigning to an empty Machine flips it Idle → Running with a fresh state age', async () => {
  await renderStation();

  // Lathe 1 (index 0) starts without assigned quantity: derived idle,
  // aged from the registry timestamp (18m — not a fresh `<1m`).
  expect(machineCard(0).classList.contains('idle')).toBe(true);
  expect(machineStat(0)).toContain('idle');
  expect(machineStat(0)).not.toContain('<1m');

  // Machine-first wizard: assign the queued 2 pcs (MAX default).
  scan('PF:MACHINE:L1');
  const dialog = activeDialog();
  fireEvent.click(
    within(dialog as HTMLElement).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm assignment' }));

  // Idle → Running immediately, and the running age starts NOW — the
  // idle timestamp is never reused for the new state.
  expect(machineCard(0).classList.contains('running')).toBe(true);
  expect(machineStat(0)).toContain('running · <1m');
  // A Machine the command did not flip keeps its state AND timestamp:
  // Lathe 2 stays running with its aged state.
  expect(machineCard(1).classList.contains('running')).toBe(true);
  expect(machineStat(1)).not.toContain('<1m');
  // Maintenance stays an explicit override — never derived away.
  expect(machineCard(3).classList.contains('maintenance')).toBe(true);
});

test('QUEUE return keeps Running while quantity remains; a full return flips Idle', async () => {
  await renderStation();

  // Lathe 3 (index 2) actively processes 3 pcs. Partial return: 1 pc
  // back to the queue — the Machine keeps processing the remainder,
  // stays Running and keeps its state age.
  expect(machineCard(2).classList.contains('running')).toBe(true);
  fireEvent.click(
    within(machineCard(2)).getByRole('button', {
      name: 'Return to Area queue',
    }),
  );
  let dialog = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity to queue',
  });
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '1' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm return to queue' }),
  );
  expect(machineCard(2).classList.contains('running')).toBe(true);
  expect(machineStat(2)).toContain('running');
  expect(machineStat(2)).not.toContain('<1m');

  // Full return of the remaining 2 pcs (MAX default): no active
  // quantity remains — Running → Idle, the idle age starts NOW.
  fireEvent.click(
    within(machineCard(2)).getByRole('button', {
      name: 'Return to Area queue',
    }),
  );
  dialog = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity to queue',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm return to queue' }),
  );
  expect(machineCard(2).classList.contains('idle')).toBe(true);
  expect(machineStat(2)).toContain('idle · <1m');
  expect(machineCard(2).textContent).toContain('No production assigned');
});

test('DONE keeps Running while assigned quantity remains; completing all of it flips Idle', async () => {
  await renderStation();

  // Partial DONE: 2 of the 3 pcs on Lathe 3 finish — 1 pc keeps
  // processing, so the Machine stays Running with its state age
  // (queued and finished quantity never count as assigned).
  fireEvent.click(
    within(machineCard(2)).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  let dialog = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm completion' }));
  expect(machineCard(2).classList.contains('running')).toBe(true);
  expect(machineStat(2)).not.toContain('<1m');

  // Full DONE of the remaining 1 pc: Running → Idle with a fresh age;
  // the finished quantity waits in the Area summary, not on the card.
  fireEvent.click(
    within(machineCard(2)).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  dialog = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm completion' }));
  expect(machineCard(2).classList.contains('idle')).toBe(true);
  expect(machineStat(2)).toContain('idle · <1m');
  expect(machineCard(2).textContent).toContain('No production assigned');
});

test('the assignment dialog checks Machine availability against the session state', async () => {
  await renderStation();

  // The Machine picker statuses follow the session derivation — after
  // Lathe 2 empties through a full QUEUE return, the wizard shows it
  // idle (the load-time mock said running).
  fireEvent.click(
    within(machineCard(1)).getByRole('button', {
      name: 'Return to Area queue',
    }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity to queue',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm return to queue' }),
  );
  expect(machineCard(1).classList.contains('idle')).toBe(true);

  scan('PF:MACHINE:L1');
  const assign = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  const lathe2Pick = within(assign as HTMLElement).getByRole('button', {
    name: /Lathe 2/,
  });
  expect(lathe2Pick.querySelector('.s')?.textContent).toBe('idle');
  fireEvent.keyDown(assign, { key: 'Escape' });
});

test('a direct-processing Area can also mark quantity DONE', async () => {
  await renderStation('EXT-ST-01');

  scan('PF:PN:142-260');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', {
      name: /Complete Area processing/,
    }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  // No Machine field for a no-Machine Area; MAX = processing quantity.
  expect(screen.getByLabelText('Quantity: 20')).toBeInTheDocument();
  // Finish only part of it: 5 pcs.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '5' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(dialog.textContent).toContain('Finished — ready to move');
  expect(dialog.textContent).not.toContain('Machine');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm completion' }));

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
    name: 'Receive from another Area',
  });
  fireEvent.keyDown(dialog, { key: 'Enter' });

  // One atomic command: completion of source processing + transfer.
  expect(dialog.textContent).toContain('Source processing');
  expect(dialog.textContent).toContain('marked complete at the source');
  // The source-processing deviation carries the warning value tone.
  expect(
    dialog.querySelector('.ss-confirm dd.tone-warn')?.textContent,
  ).toContain('marked complete at the source');
  expect(dialog.textContent).toContain('AREA_COMPLETED, then TRANSFERRED');
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
    name: 'Receive from another Area',
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
    name: 'Receive from another Area',
  });
  // Move only 2 of the 3 pcs.
  fireEvent.keyDown(dialog, { key: 'Delete' });
  fireEvent.keyDown(dialog, { key: '2' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  // The remaining 1 pc stays at the source in its existing state. The
  // transferred quantity is now IN this Area, so the next scan opens
  // the PN action dialog; its receive choice lists the sources.
  scan('PF:PN:78-04-0031');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).toHaveTextContent('1 pcs at Mill');
  expect(actions).toHaveTextContent('3 pcs at Deburr');
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
    name: 'Reverse the last Part Number action?',
  });
  // The Undo summary names the COMPLETE command, not one arbitrary row.
  expect(dialog).toHaveTextContent('AREA_COMPLETED + TRANSFERRED');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm reversal' }));

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
    name: 'Receive from another Area',
  });
  const cancel = within(dialog as HTMLElement).getByRole('button', {
    name: 'Cancel (Esc)',
  });
  expect(cancel.textContent).toBe('Cancel (Esc)'); // no long suffixes
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // The manual PN entry dialog shares the standard wizard buttons
  // (v15): the same Cancel label plus a primary `Continue` action.
  fireEvent.click(screen.getByRole('button', { name: '⌨ Enter PN manually' }));
  const manual = await screen.findByRole('dialog', {
    name: 'Enter Part Number manually',
  });
  expect(
    within(manual as HTMLElement).getByRole('button', {
      name: 'Cancel (Esc)',
    }).textContent,
  ).toBe('Cancel (Esc)');
  expect(
    within(manual as HTMLElement).getByRole('button', { name: 'Continue' }),
  ).toHaveClass('primary');
  fireEvent.keyDown(manual, { key: 'Escape' });
});

/* ============ Presentation contracts (stylesheet) ============ */

test('Undo is a shared full-height action zone beside its separator; the shared chips align in dialog recaps', async () => {
  const { readFileSync } = await import('node:fs');
  const { dirname, join } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const here = dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(join(here, 'scan-station.css'), 'utf8');
  const globalCss = readFileSync(
    join(here, '..', '..', 'styles', 'global.css'),
    'utf8',
  );

  // The shared action-zone presentation (Undo + offline Retry):
  // transparent at rest (reads as the owning surface's own
  // background), a light error surface highlights in on hover, with
  // one active / focus-visible / disabled treatment, never button
  // chrome.
  const zone = /\.zone-action \{[^}]*}/s.exec(globalCss)![0];
  expect(zone).toContain('border: none');
  expect(zone).toContain('background: transparent');
  const zoneHover = /\.zone-action:hover:not\(:disabled\) \{[^}]*}/s.exec(
    globalCss,
  )![0];
  expect(zoneHover).toContain('background: var(--error-surface)');
  expect(globalCss).toMatch(/\.zone-action:active:not\(:disabled\) \{[^}]*}/s);
  expect(globalCss).toMatch(/\.zone-action:focus-visible \{[^}]*outline/s);
  expect(globalCss).toMatch(/\.zone-action:disabled \{[^}]*opacity/s);

  // Undo action region: the whole right region is the button — full
  // block height, flush right edge, centered content, divided from
  // the information region by an inset vertical rule (never a
  // full-height border touching the block's top/bottom) — the click
  // target itself still reaches flush to the block's border and right
  // up to that rule.
  const undo = /\.ss-lastpn \.ss-undo \{[^}]*}/s.exec(css)![0];
  expect(undo).toContain('align-self: stretch');
  expect(undo).not.toContain('border-left');
  expect(undo).toContain('align-items: center');
  expect(undo).toContain('justify-content: center');
  expect(undo).not.toContain('min-height: 44px');
  const undoSep = /\.ss-lastpn \.ss-undo::before \{[^}]*}/s.exec(css)![0];
  expect(undoSep).toContain("content: ''");
  expect(undoSep).toContain('position: absolute');
  expect(undoSep).toMatch(/top:\s*\d/);
  expect(undoSep).toMatch(/bottom:\s*\d/);
  expect(css).not.toContain('.ss-undosep');
  // The label lives OUTSIDE the block; the block clips its children
  // (no leftover background right of the action region), keeps no
  // bottom margin (the parent padding is the only outer spacing) and
  // no own padding — the regions carry their own.
  const block = /\.ss-lastpn \{[^}]*}/s.exec(css)![0];
  expect(block).toContain('margin: 0');
  expect(block).toContain('padding: 0');
  expect(block).toContain('overflow: hidden');
  // Equal-height contract for the shared chips inside dialog recaps —
  // and the DEFAULT RouteModeChip matches the TypeChip metrics while
  // Tracking's flow header keeps its compact variant.
  const chips =
    /\.ss-recap \.typechip,\s*\.ss-recap \.routechip \{[^}]*}/s.exec(css)![0];
  expect(chips).toContain('display: inline-flex');
  expect(chips).toContain('min-height');
  const routechip = /\.routechip \{[^}]*}/s.exec(globalCss)![0];
  expect(routechip).toContain('display: inline-flex');
  expect(routechip).toContain('font-size: 11px');
});

test('the header wrap is measurement-driven and the Worker pill is content-sized', async () => {
  const { readFileSync } = await import('node:fs');
  const { dirname, join } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const here = dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(join(here, 'scan-station.css'), 'utf8');

  // No hard-coded header breakpoint: the wrapped state is a class set
  // by the fit measurement in ScanStationView, never a fixed-width
  // container or media query.
  expect(css).not.toContain('1179px');
  expect(css).not.toContain('1220px');
  const wrapped = /\.ss-head\.wrapped \.ss-stats \{[^}]*}/s.exec(css)![0];
  expect(wrapped).toContain('grid-column: 1 / -1');
  expect(wrapped).toContain('grid-row: 2');
  // The probe state restores single-row placement for the measurement
  // pass (declared after the wrapped rules so it wins while both
  // classes apply) on FULL natural column widths — the identity floor
  // keeps the Operations chips unwrapped, so chips never wrap before
  // the totals drop.
  const probeCols = /\.ss-head\.measuring \{[^}]*}/s.exec(css)![0];
  expect(probeCols).toContain(
    'grid-template-columns: max-content max-content max-content',
  );
  expect(css).toMatch(
    /\.ss-head\.measuring \.op,\s*\.ss-head\.measuring \.opchips \{[^}]*flex-wrap: nowrap/s,
  );
  const probe = /\.ss-head\.measuring \.ss-stats \{[^}]*}/s.exec(css)![0];
  expect(probe).toContain('grid-column: auto');
  expect(css.indexOf('.ss-head.measuring')).toBeGreaterThan(
    css.indexOf('.ss-head.wrapped'),
  );

  // Content-sized Worker Session pill: no fixed or minimum width, and
  // nowrap keeps its label / name / session-window lines intact.
  const pill = /\.ss-pill \{[^}]*}/s.exec(css)![0];
  expect(pill).not.toMatch(/min-width\s*:/);
  expect(pill).not.toMatch(/(^|[^-])width\s*:/m);
  expect(pill).toContain('white-space: nowrap');
});

test('the shared DevNotice fills its parent width with one content flow', async () => {
  const { readFileSync } = await import('node:fs');
  const { dirname, join } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const here = dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(
    join(here, '..', '..', 'styles', 'global.css'),
    'utf8',
  );
  const notice = /\.dev-notice \{[^}]*}/s.exec(css)![0];
  // No max-width DECLARATION caps the notice (comments may mention it).
  expect(notice).not.toMatch(/max-width\s*:/);
  expect(notice).toContain('width: 100%');
  expect(notice).toContain('box-sizing: border-box');
  expect(notice).toContain('min-width: 0');
  // One inline content flow; only the individual barcode values are
  // kept unbreakable.
  expect(css).toMatch(/\.dev-notice \.dev-notice-content \{[^}]*min-width: 0/s);
  const code = /\.dev-notice code \{[^}]*}/s.exec(css)![0];
  expect(code).toContain('white-space: nowrap');
});

/* ============ Consistent Back across dialog workflows ============ */

// Back must return to the exact previous dialog/step of the same
// one-shot workflow (context preserved) and must never record or
// change tracking data; flows entered directly from the Scan Station
// surface show no Back at all — Cancel stays the only exit there.

function manualEnter(pn: string) {
  fireEvent.click(screen.getByRole('button', { name: '⌨ Enter PN manually' }));
  const field = screen.getByLabelText('Part Number');
  fireEvent.change(field, { target: { value: pn } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
}

function manualFieldValue() {
  return (screen.getByLabelText('Part Number') as HTMLInputElement).value;
}

test('Back returns from the action dialog to manual PN entry with the PN preserved', async () => {
  await renderStation();

  manualEnter('2027-60-8114-00');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );

  await screen.findByRole('dialog', { name: 'Enter Part Number manually' });
  expect(manualFieldValue()).toBe('2027-60-8114-00');
  // Back is navigation only — no write, and no cancel notice.
  expect(lastPnText()).toBe('—');
  expect(screen.queryByText('Cancelled. No changes were recorded.')).toBeNull();

  // Forward again from the preserved value.
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(
    await screen.findByRole('dialog', { name: 'Select an action' }),
  ).toBeInTheDocument();
});

test('Back returns from the receive wizard to manual PN entry; the scanned wizard has no Back', async () => {
  await renderStation();

  manualEnter('NEW-PART-01');
  const wizard = await screen.findByRole('dialog', {
    name: 'Receive Quantity',
  });
  fireEvent.click(
    within(wizard as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  await screen.findByRole('dialog', { name: 'Enter Part Number manually' });
  expect(manualFieldValue()).toBe('NEW-PART-01');
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });

  // The same wizard resolved from a scan has no previous dialog step.
  scan('PF:PN:NEW-PART-01');
  const scanned = await screen.findByRole('dialog', {
    name: 'Receive Quantity',
  });
  expect(
    within(scanned as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();
  fireEvent.keyDown(scanned, { key: 'Escape' });
  expect(lastPnText()).toBe('—');
});

test('Back returns from the transfer quantity view to manual PN entry; a direct scan has none there', async () => {
  await renderStation();

  manualEnter('118-052'); // one source (Manual) → straight to quantity
  let dialog = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(
    within(dialog as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  await screen.findByRole('dialog', { name: 'Enter Part Number manually' });
  expect(manualFieldValue()).toBe('118-052');
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });

  // Scanned entry: the quantity view shows no Back (the confirmation
  // view's internal Back to the quantity view is separate and stays).
  scan('PF:PN:118-052');
  dialog = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(
    within(dialog as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(lastPnText()).toBe('—');
});

test('transfer Back returns to the source selection with all sources intact', async () => {
  await renderStation();

  scan('PF:PN:78-04-0031'); // Mill + Deburr
  const select = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  // Direct scan entry: the selection view itself has no parent step.
  expect(
    within(select as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: /Deburr — 3 pcs/ }));
  const qty = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(qty).toHaveTextContent('Transfer Deburr → Lathe');
  fireEvent.click(
    within(qty as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );

  // Back re-opens the SAME selection — both sources again, no write —
  // and picking the other source carries the corrected context forward.
  const again = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  expect(again).toHaveTextContent('Mill — 3 pcs available');
  expect(again).toHaveTextContent('Deburr — 3 pcs available');
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  const qty2 = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(qty2).toHaveTextContent('Transfer Mill → Lathe');
  expect(lastPnText()).toBe('—');
});

test('Back walks the exact entry chain: manual entry → source selection → transfer', async () => {
  await renderStation();

  manualEnter('78-04-0031');
  const select = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  // Opened from manual entry, the selection view DOES offer Back.
  expect(
    within(select as HTMLElement).getByRole('button', { name: '‹ Back' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Mill — 3 pcs/ }));
  const qty = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(
    within(qty as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  const back = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  fireEvent.click(
    within(back as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  await screen.findByRole('dialog', { name: 'Enter Part Number manually' });
  expect(manualFieldValue()).toBe('78-04-0031');
  expect(lastPnText()).toBe('—');
});

test('every child of the action dialog goes Back to it with the PN context intact', async () => {
  await renderStation();

  scan('PF:PN:0455-20-0118-03');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  // Opened by a scan: the action dialog itself has no Back.
  expect(
    within(actions as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();

  async function openAndBack(choice: RegExp, dialogName: string) {
    fireEvent.click(
      within(activeDialog() as HTMLElement).getByRole('button', {
        name: choice,
      }),
    );
    const child = await screen.findByRole('dialog', { name: dialogName });
    expect(child).toHaveTextContent('0455-20-0118-03');
    fireEvent.click(
      within(child as HTMLElement).getByRole('button', { name: '‹ Back' }),
    );
    const back = await screen.findByRole('dialog', {
      name: 'Select an action',
    });
    expect(back).toHaveTextContent('0455-20-0118-03');
  }

  await openAndBack(/Add more quantity/, 'Add more quantity');
  await openAndBack(/Return quantity for repair/, 'Return quantity for repair');
  await openAndBack(/Scrap damaged quantity/, 'Scrap damaged quantity');
  // Back is navigation only — nothing was recorded in the whole loop.
  expect(lastPnText()).toBe('—');
});

test('DONE from the action dialog goes Back to it; the row DONE enters without Back', async () => {
  await renderStation('DEBURR-ST-01');

  scan('PF:PN:81-1042');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', {
      name: /Complete Area processing/,
    }),
  );
  const done = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(
    within(done as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Select an action' }),
  ).toBeInTheDocument();
  fireEvent.keyDown(activeDialog(), { key: 'Escape' });

  // The row DONE enters directly from the monitoring surface — no Back.
  fireEvent.click(
    screen.getByRole('button', { name: 'Complete Area processing' }),
  );
  const rowDone = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  expect(
    within(rowDone as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();
  fireEvent.keyDown(rowDone, { key: 'Escape' });
  expect(lastPnText()).toBe('—');
});

test('the assignment wizard Back re-enters the action dialog and keeps the chain to manual entry', async () => {
  await renderStation();

  manualEnter('2027-60-8114-00');
  await screen.findByRole('dialog', { name: 'Select an action' });
  fireEvent.click(
    within(activeDialog() as HTMLElement).getByRole('button', {
      name: /Assign queued quantity/,
    }),
  );
  const assign = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  fireEvent.click(
    within(assign as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  // The re-opened action dialog still knows ITS parent: Back again
  // reaches manual entry with the PN preserved.
  fireEvent.click(
    within(actions as HTMLElement).getByRole('button', { name: '‹ Back' }),
  );
  await screen.findByRole('dialog', { name: 'Enter Part Number manually' });
  expect(manualFieldValue()).toBe('2027-60-8114-00');
  expect(lastPnText()).toBe('—');
});

test('workflows entered directly from the station surface never show a fake Back', async () => {
  await renderStation();

  // Machine-first assignment (Machine scan) — no previous dialog step.
  scan('PF:MACHINE:L1');
  let dialog = activeDialog();
  expect(
    within(dialog as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // Machine-row QUEUE return — entered from the monitoring surface.
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Return to Area queue' })[0],
  );
  dialog = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity to queue',
  });
  expect(
    within(dialog as HTMLElement).queryByRole('button', { name: '‹ Back' }),
  ).toBeNull();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(lastPnText()).toBe('—');
});
