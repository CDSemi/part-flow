import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Scan Station regressions: faint Station ID diagnostics, the visible
// manual-PN fallback, separated Movement type display, physical
// keyboard quantity entry, and input refocus after reconnect.

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

async function renderScanStation() {
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);
  return await screen.findByLabelText('Scan barcode');
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

test('the Station ID renders only as a faint bottom-edge diagnostic', async () => {
  await renderScanStation();

  const mentions = screen.getAllByText(/LATHE-ST-01/);
  expect(mentions).toHaveLength(1);
  expect(mentions[0].closest('.ss-stationfoot')).not.toBeNull();
  // The header keeps Department / Area / Operations prominent instead.
  expect(screen.getByText('Machine Shop')).toBeInTheDocument();
  expect(screen.getAllByText('Lathe').length).toBeGreaterThan(0);
});

test('manual PN entry is a visible secondary action with its own validated flow', async () => {
  await renderScanStation();

  const button = await screen.findByRole('button', {
    name: '⌨ Enter PN manually',
  });
  expect(
    screen.getByText(/Fallback when the scanner is unavailable/),
  ).toBeInTheDocument();
  fireEvent.click(button);

  const field = screen.getByLabelText('Exact PartNumber');
  // Raw PN text is validated as a PN — never treated as barcode input.
  fireEvent.change(field, { target: { value: 'NOPE-123' } });
  fireEvent.keyDown(field, { key: 'Enter' });
  expect(screen.getByText('Unknown PartNumber')).toBeInTheDocument();
  expect(screen.getByText(/nothing recorded/)).toBeInTheDocument();
});

test('recent scans separate the Movement type from PN and description', async () => {
  await renderScanStation();

  // The Movement type renders as its own badge…
  const badges = screen.getAllByText('ASSIGNED_TO_MACHINE');
  expect(badges.length).toBeGreaterThanOrEqual(2);
  for (const badge of badges) {
    expect(badge.className).toContain('mvtype');
  }
  // …and each record shows its recorded status separately.
  expect(screen.getAllByText('✓ recorded').length).toBeGreaterThanOrEqual(3);
  // The description no longer embeds the Movement type prefix.
  expect(screen.getByText('Lathe queue → Lathe 3 · qty 4')).toBeInTheDocument();
  expect(screen.queryByText(/ASSIGNED_TO_MACHINE · Lathe/)).toBeNull();
});

test('the quantity dialog accepts the physical keyboard', async () => {
  await renderScanStation();

  scan('PF:PN:1014');
  const dialog = await screen.findByRole('dialog', {
    name: 'Enter quantity',
  });

  fireEvent.keyDown(dialog, { key: '4' });
  fireEvent.keyDown(dialog, { key: '2' });
  expect(
    screen.getByRole('status', { name: 'Quantity: 42' }),
  ).toBeInTheDocument();

  fireEvent.keyDown(dialog, { key: 'Backspace' });
  expect(
    screen.getByRole('status', { name: 'Quantity: 4' }),
  ).toBeInTheDocument();

  fireEvent.keyDown(dialog, { key: 'Delete' });
  expect(
    screen.getByRole('status', { name: 'Quantity: none' }),
  ).toBeInTheDocument();

  // Enter with an empty/invalid value confirms nothing.
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(
    screen.getByRole('dialog', { name: 'Enter quantity' }),
  ).toBeInTheDocument();

  // An excessive quantity is still rejected clearly.
  fireEvent.keyDown(dialog, { key: '9' });
  fireEvent.keyDown(dialog, { key: '9' });
  fireEvent.keyDown(dialog, { key: 'Enter' });
  expect(
    screen.getByText('Quantity exceeds available source quantity'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  // A valid quantity confirms with Enter.
  scan('PF:PN:1014');
  const dialog2 = await screen.findByRole('dialog', {
    name: 'Enter quantity',
  });
  fireEvent.keyDown(dialog2, { key: '4' });
  fireEvent.keyDown(dialog2, { key: 'Enter' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText(/0455-20-0118-03 × 4/)).toBeInTheDocument();
});

test('Escape cancels the quantity dialog without recording anything', async () => {
  await renderScanStation();

  scan('PF:PN:1014');
  const dialog = await screen.findByRole('dialog', {
    name: 'Enter quantity',
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Cancelled — nothing recorded.')).toBeInTheDocument();
});

test('recovery re-enables and refocuses the scan input', async () => {
  failing = true;
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);
  await screen.findByText('OFFLINE');
  const input = await screen.findByLabelText('Scan barcode');
  expect(input).toBeDisabled();

  failing = false;
  fireEvent(window, new Event('online'));

  await waitFor(() => expect(input).toBeEnabled());
  await waitFor(() => expect(input).toHaveFocus());
});
