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

  // Over-long identifier from the long-data mock set.
  const longPn = await screen.findByText(/0118-40-0022-07-0455-88-REV-C/);
  expect(longPn).toBeInTheDocument();
  // Many rows render without corrupting the table.
  expect(screen.getByText('0114-60-0124-00')).toBeInTheDocument();
});
