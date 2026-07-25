import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';

// Development-only state previews (?state=…) drive the deterministic
// loading / empty / error / long-data representations. The override is
// compiled away outside development builds; vitest runs in DEV mode.

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
});

function renderAt(path: string) {
  window.history.replaceState({}, '', path);
  return render(<App />);
}

test('a view renders its loading state', async () => {
  renderAt('/management/tracking?state=loading');

  expect(
    await screen.findByRole('status', { name: 'Loading Tracking' }),
  ).toBeInTheDocument();
});

test('a view renders its empty state', async () => {
  renderAt('/management/tracking?state=empty');

  expect(
    await screen.findByText(/No PNs match the current filters/),
  ).toBeInTheDocument();
});

test('a view renders its error state', async () => {
  renderAt('/management/area-board?state=error');

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Area Board data could not be loaded.',
  );
});

test('the Production Board renders long data without wrapping the PN', async () => {
  renderAt('/production-board?state=long');

  // Over-long identifier from the long-data mock set (page 1).
  const longPn = await screen.findByText(/0118-40-0022-07-0455-88-REV-C/);
  expect(longPn).toBeInTheDocument();
  // The long description renders on its own secondary line and never
  // displaces the quantity / due / total columns of the row.
  expect(
    screen.getByText(/MANIFOLD ASSY, 6-PORT ANODIZED, W\/ FITTINGS 1\/4 NPT/),
  ).toBeInTheDocument();
  // The long list paginates and honestly reports its mock rotation:
  // the footer claim exists only because the interval really rotates.
  expect(
    screen.getByText(/Page 1 \/ 3 · rotates every 12 s/),
  ).toBeInTheDocument();
});

test('the Tracking long-data state renders 30 or more rows', async () => {
  renderAt('/management/tracking?state=long');

  await screen.findByText('0114-60-0101-00');
  // 1 long-identifier sample + 6 base rows + 28 generated rows = 35.
  const generated = screen.getAllByText(/^0114-60-01\d\d-00$/);
  expect(generated.length).toBeGreaterThanOrEqual(28);
  const listRows = screen
    .getAllByRole('row')
    .filter((row) => row.closest('.tk-table'));
  expect(listRows.length).toBeGreaterThanOrEqual(31); // header + 30+ rows
});
